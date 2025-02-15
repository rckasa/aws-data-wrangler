import logging

import pandas

from pyspark.sql.functions import pandas_udf, PandasUDFType, spark_partition_id
from pyspark.sql.types import TimestampType

from awswrangler.exceptions import MissingBatchDetected, UnsupportedFileFormat

logger = logging.getLogger(__name__)

MIN_NUMBER_OF_ROWS_TO_DISTRIBUTE = 1000


class Spark:
    def __init__(self, session):
        self._session = session

    def read_csv(self, **args):
        spark = self._session.spark_session
        return spark.read.csv(**args)

    @staticmethod
    def _extract_casts(dtypes):
        casts = {}
        for col, dtype in dtypes:
            if dtype in ["smallint", "int", "bigint"]:
                casts[col] = "Int64"
            elif dtype == "object":
                casts[col] = "str"
        logger.debug(f"casts: {casts}")
        return casts

    @staticmethod
    def date2timestamp(dataframe):
        for col, dtype in dataframe.dtypes:
            if dtype == "date":
                dataframe = dataframe.withColumn(
                    col, dataframe[col].cast(TimestampType()))
                logger.warning(f"Casting column {col} from date to timestamp!")
        return dataframe

    def to_redshift(
            self,
            dataframe,
            path,
            connection,
            schema,
            table,
            iam_role,
            diststyle="AUTO",
            distkey=None,
            sortstyle="COMPOUND",
            sortkey=None,
            min_num_partitions=200,
            mode="append",
    ):
        """
        Load Spark Dataframe as a Table on Amazon Redshift

        :param dataframe: Pandas Dataframe
        :param path: S3 path to write temporary files (E.g. s3://BUCKET_NAME/ANY_NAME/)
        :param connection: A PEP 249 compatible connection (Can be generated with Redshift.generate_connection())
        :param schema: The Redshift Schema for the table
        :param table: The name of the desired Redshift table
        :param iam_role: AWS IAM role with the related permissions
        :param diststyle: Redshift distribution styles. Must be in ["AUTO", "EVEN", "ALL", "KEY"] (https://docs.aws.amazon.com/redshift/latest/dg/t_Distributing_data.html)
        :param distkey: Specifies a column name or positional number for the distribution key
        :param sortstyle: Sorting can be "COMPOUND" or "INTERLEAVED" (https://docs.aws.amazon.com/redshift/latest/dg/t_Sorting_data.html)
        :param sortkey: List of columns to be sorted
        :param min_num_partitions: Minimal number of partitions
        :param mode: append or overwrite
        :return: None
        """
        logger.debug(f"Minimum number of partitions : {min_num_partitions}")
        if path[-1] != "/":
            path += "/"
        self._session.s3.delete_objects(path=path)
        spark = self._session.spark_session
        dataframe = Spark.date2timestamp(dataframe)
        dataframe.cache()
        num_rows = dataframe.count()
        logger.info(f"Number of rows: {num_rows}")
        if num_rows < MIN_NUMBER_OF_ROWS_TO_DISTRIBUTE:
            num_partitions = 1
        else:
            num_slices = self._session.redshift.get_number_of_slices(
                redshift_conn=connection)
            logger.debug(f"Number of slices on Redshift: {num_slices}")
            num_partitions = num_slices
            while num_partitions < min_num_partitions:
                num_partitions += num_slices
        logger.debug(f"Number of partitions calculated: {num_partitions}")
        spark.conf.set("spark.sql.execution.arrow.enabled", "true")
        session_primitives = self._session.primitives
        casts = Spark._extract_casts(dataframe.dtypes)

        @pandas_udf(returnType="objects_paths string",
                    functionType=PandasUDFType.GROUPED_MAP)
        def write(pandas_dataframe):
            del pandas_dataframe["aws_data_wrangler_internal_partition_id"]
            paths = session_primitives.session.pandas.to_parquet(
                dataframe=pandas_dataframe,
                path=path,
                preserve_index=False,
                mode="append",
                procs_cpu_bound=1,
                cast_columns=casts)
            return pandas.DataFrame.from_dict({"objects_paths": paths})

        df_objects_paths = dataframe.repartition(numPartitions=num_partitions) \
            .withColumn("aws_data_wrangler_internal_partition_id", spark_partition_id()) \
            .groupby("aws_data_wrangler_internal_partition_id") \
            .apply(write)

        objects_paths = list(df_objects_paths.toPandas()["objects_paths"])
        dataframe.unpersist()
        num_files_returned = len(objects_paths)
        if num_files_returned != num_partitions:
            raise MissingBatchDetected(
                f"{num_files_returned} files returned. {num_partitions} expected."
            )
        logger.debug(f"List of objects returned: {objects_paths}")
        logger.debug(
            f"Number of objects returned from UDF: {num_files_returned}")
        manifest_path = f"{path}manifest.json"
        self._session.redshift.write_load_manifest(manifest_path=manifest_path,
                                                   objects_paths=objects_paths)
        self._session.redshift.load_table(
            dataframe=dataframe,
            dataframe_type="spark",
            manifest_path=manifest_path,
            schema_name=schema,
            table_name=table,
            redshift_conn=connection,
            preserve_index=False,
            num_files=num_partitions,
            iam_role=iam_role,
            diststyle=diststyle,
            distkey=distkey,
            sortstyle=sortstyle,
            sortkey=sortkey,
            mode=mode,
        )
        self._session.s3.delete_objects(path=path)

    def create_glue_table(self,
                          database,
                          path,
                          dataframe,
                          file_format,
                          compression,
                          table=None,
                          serde=None,
                          sep=",",
                          partition_by=None,
                          load_partitions=True,
                          replace_if_exists=True):
        """
        Create a Glue metadata table pointing for some dataset stored on AWS S3.

        :param dataframe: PySpark Dataframe
        :param file_format: File format (E.g. "parquet", "csv")
        :param partition_by: Columns used for partitioning
        :param path: AWS S3 path
        :param compression: Compression (e.g. gzip, snappy, lzo, etc)
        :param sep: Separator token for CSV formats (e.g. ",", ";", "|")
        :param serde: Serializer/Deserializer (e.g. "OpenCSVSerDe", "LazySimpleSerDe")
        :param database: Glue database name
        :param table: Glue table name. If not passed, extracted from the path
        :param load_partitions: Load partitions after the table creation
        :param replace_if_exists: Drop table and recreates that if already exists
        :return: None
        """
        file_format = file_format.lower()
        if file_format not in ["parquet", "csv"]:
            raise UnsupportedFileFormat(file_format)
        table = table if table else self._session.glue.parse_table_name(path)
        table = table.lower().replace(".", "_")
        logger.debug(f"table: {table}")
        full_schema = dataframe.dtypes
        if partition_by is None:
            partition_by = []
        schema = [x for x in full_schema if x[0] not in partition_by]
        partitions_schema_tmp = {
            x[0]: x[1]
            for x in full_schema if x[0] in partition_by
        }
        partitions_schema = [(x, partitions_schema_tmp[x])
                             for x in partition_by]
        logger.debug(f"schema: {schema}")
        logger.debug(f"partitions_schema: {partitions_schema}")
        if replace_if_exists is not None:
            self._session.glue.delete_table_if_exists(database=database,
                                                      table=table)
        extra_args = {}
        if file_format == "csv":
            extra_args["sep"] = sep
            if serde is None:
                serde = "OpenCSVSerDe"
            extra_args["serde"] = serde
        self._session.glue.create_table(
            database=database,
            table=table,
            schema=schema,
            partition_cols_schema=partitions_schema,
            path=path,
            file_format=file_format,
            compression=compression,
            extra_args=extra_args)
        if load_partitions:
            self._session.athena.repair_table(database=database, table=table)
