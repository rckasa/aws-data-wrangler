from math import ceil
import re
import logging
from datetime import datetime, date

import pyarrow

from awswrangler.exceptions import UnsupportedType, UnsupportedFileFormat, InvalidSerDe, ApiError

logger = logging.getLogger(__name__)


class Glue:
    def __init__(self, session):
        self._session = session
        self._client_glue = session.boto3_session.client(
            service_name="glue", config=session.botocore_config)

    def get_table_athena_types(self, database, table):
        """
        Get all columns names and the related data types

        :param database: Glue database's name
        :param table: Glue table's name
        :return: A dictionary as {"col name": "col dtype"}
        """
        response = self._client_glue.get_table(DatabaseName=database,
                                               Name=table)
        logger.debug(f"get_table response:\n{response}")
        dtypes = {}
        for col in response["Table"]["StorageDescriptor"]["Columns"]:
            dtypes[col["Name"]] = col["Type"]
        for par in response["Table"]["PartitionKeys"]:
            dtypes[par["Name"]] = par["Type"]
        return dtypes

    def get_table_python_types(self, database, table):
        """
        Get all columns names and the related python types

        :param database: Glue database's name
        :param table: Glue table's name
        :return: A dictionary as {"col name": "col python type"}
        """
        dtypes = self.get_table_athena_types(database=database, table=table)
        return {k: Glue.type_athena2python(v) for k, v in dtypes.items()}

    @staticmethod
    def type_athena2pyarrow(dtype):
        dtype = dtype.lower()
        if dtype == "tinyint":
            return "int8"
        if dtype == "smallint":
            return "int16"
        elif dtype in ["int", "integer"]:
            return "int32"
        elif dtype == "bigint":
            return "int64"
        elif dtype == "float":
            return "float32"
        elif dtype == "double":
            return "float64"
        elif dtype in ["boolean", "bool"]:
            return "bool"
        elif dtype in ["string", "char", "varchar", "array", "row", "map"]:
            return "string"
        elif dtype == "timestamp":
            return "timestamp[ns]"
        elif dtype == "date":
            return "date32"
        else:
            raise UnsupportedType(f"Unsupported Athena type: {dtype}")

    @staticmethod
    def type_pyarrow2athena(dtype):
        dtype_str = str(dtype).lower()
        if dtype_str == "int32":
            return "int"
        elif dtype_str == "int64":
            return "bigint"
        elif dtype_str == "float":
            return "float"
        elif dtype_str == "double":
            return "double"
        elif dtype_str == "bool":
            return "boolean"
        elif dtype_str == "string":
            return "string"
        elif dtype_str.startswith("timestamp"):
            return "timestamp"
        elif dtype_str.startswith("date"):
            return "date"
        elif dtype_str.startswith("list"):
            return f"array<{Glue.type_pyarrow2athena(dtype.value_type)}>"
        else:
            raise UnsupportedType(f"Unsupported Pyarrow type: {dtype}")

    @staticmethod
    def type_pandas2athena(dtype):
        dtype = dtype.lower()
        if dtype == "int32":
            return "int"
        elif dtype in ["int64", "Int64"]:
            return "bigint"
        elif dtype == "float32":
            return "float"
        elif dtype == "float64":
            return "double"
        elif dtype == "bool":
            return "boolean"
        elif dtype == "object":
            return "string"
        elif dtype.startswith("datetime64"):
            return "timestamp"
        else:
            raise UnsupportedType(f"Unsupported Pandas type: {dtype}")

    @staticmethod
    def type_athena2python(dtype):
        dtype = dtype.lower()
        if dtype in ["int", "integer", "bigint", "smallint", "tinyint"]:
            return int
        elif dtype in ["float", "double", "real"]:
            return float
        elif dtype == "boolean":
            return bool
        elif dtype in ["string", "char", "varchar", "array", "row", "map"]:
            return str
        elif dtype == "timestamp":
            return datetime
        elif dtype == "date":
            return date
        else:
            raise UnsupportedType(f"Unsupported Athena type: {dtype}")

    @staticmethod
    def type_python2athena(python_type):
        python_type = str(python_type)
        if python_type == "<class 'int'>":
            return "bigint"
        elif python_type == "<class 'float'>":
            return "double"
        elif python_type == "<class 'boll'>":
            return "boolean"
        elif python_type == "<class 'str'>":
            return "string"
        elif python_type == "<class 'datetime.datetime'>":
            return "timestamp"
        elif python_type == "<class 'datetime.date'>":
            return "date"
        else:
            raise UnsupportedType(f"Unsupported Python type: {python_type}")

    def metadata_to_glue(self,
                         dataframe,
                         path,
                         objects_paths,
                         file_format,
                         database=None,
                         table=None,
                         partition_cols=None,
                         preserve_index=True,
                         mode="append",
                         compression=None,
                         cast_columns=None,
                         extra_args=None):
        schema, partition_cols_schema = Glue._build_schema(
            dataframe=dataframe,
            partition_cols=partition_cols,
            preserve_index=preserve_index,
            cast_columns=cast_columns)
        table = table if table else Glue.parse_table_name(path)
        table = table.lower().replace(".", "_")
        if mode == "overwrite":
            self.delete_table_if_exists(database=database, table=table)
        exists = self.does_table_exists(database=database, table=table)
        if not exists:
            self.create_table(database=database,
                              table=table,
                              schema=schema,
                              partition_cols_schema=partition_cols_schema,
                              path=path,
                              file_format=file_format,
                              compression=compression,
                              extra_args=extra_args)
        if partition_cols:
            partitions_tuples = Glue._parse_partitions_tuples(
                objects_paths=objects_paths, partition_cols=partition_cols)
            self.add_partitions(database=database,
                                table=table,
                                partition_paths=partitions_tuples,
                                file_format=file_format,
                                compression=compression,
                                extra_args=extra_args)

    def delete_table_if_exists(self, database, table):
        try:
            self._client_glue.delete_table(DatabaseName=database, Name=table)
        except self._client_glue.exceptions.EntityNotFoundException:
            pass

    def does_table_exists(self, database, table):
        try:
            self._client_glue.get_table(DatabaseName=database, Name=table)
            return True
        except self._client_glue.exceptions.EntityNotFoundException:
            return False

    def create_table(self,
                     database,
                     table,
                     schema,
                     path,
                     file_format,
                     compression,
                     partition_cols_schema=None,
                     extra_args=None):
        if file_format == "parquet":
            table_input = Glue.parquet_table_definition(
                table, partition_cols_schema, schema, path, compression)
        elif file_format == "csv":
            table_input = Glue.csv_table_definition(table,
                                                    partition_cols_schema,
                                                    schema,
                                                    path,
                                                    compression,
                                                    extra_args=extra_args)
        else:
            raise UnsupportedFileFormat(file_format)
        self._client_glue.create_table(DatabaseName=database,
                                       TableInput=table_input)

    def add_partitions(self, database, table, partition_paths, file_format,
                       compression, extra_args):
        if not partition_paths:
            return None
        partitions = list()
        for partition in partition_paths:
            if file_format == "parquet":
                partition_def = Glue.parquet_partition_definition(
                    partition=partition, compression=compression)
            elif file_format == "csv":
                partition_def = Glue.csv_partition_definition(
                    partition=partition,
                    compression=compression,
                    extra_args=extra_args)
            else:
                raise UnsupportedFileFormat(file_format)
            partitions.append(partition_def)
        pages_num = int(ceil(len(partitions) / 100.0))
        for _ in range(pages_num):
            page = partitions[:100]
            del partitions[:100]
            res = self._client_glue.batch_create_partition(
                DatabaseName=database,
                TableName=table,
                PartitionInputList=page)
            for error in res["Errors"]:
                if "ErrorDetail" in error:
                    if "ErrorCode" in error["ErrorDetail"]:
                        if error["ErrorDetail"][
                                "ErrorCode"] != "AlreadyExistsException":
                            raise ApiError(f"{error}")

    def get_connection_details(self, name):
        return self._client_glue.get_connection(
            Name=name, HidePassword=False)["Connection"]

    @staticmethod
    def _extract_pyarrow_schema(dataframe, preserve_index):
        cols = []
        cols_dtypes = {}
        schema = []

        for name, dtype in dataframe.dtypes.to_dict().items():
            dtype = str(dtype)
            if dtype == "Int64":
                cols_dtypes[name] = "int64"
            else:
                cols.append(name)

        for field in pyarrow.Schema.from_pandas(df=dataframe[cols],
                                                preserve_index=preserve_index):
            name = str(field.name)
            dtype = field.type
            cols_dtypes[name] = dtype
            if name not in dataframe.columns:
                schema.append((name, dtype))

        schema += [(name, cols_dtypes[name]) for name in dataframe.columns]
        logger.debug(f"schema: {schema}")
        return schema

    @staticmethod
    def _build_schema(dataframe,
                      partition_cols,
                      preserve_index,
                      cast_columns={}):
        logger.debug(f"dataframe.dtypes:\n{dataframe.dtypes}")
        if not partition_cols:
            partition_cols = []

        pyarrow_schema = Glue._extract_pyarrow_schema(
            dataframe=dataframe, preserve_index=preserve_index)

        schema_built = []
        partition_cols_types = {}
        for name, dtype in pyarrow_schema:
            if (cast_columns is not None) and (name in cast_columns.keys()):
                if name in partition_cols:
                    partition_cols_types[name] = cast_columns[name]
                else:
                    schema_built.append((name, cast_columns[name]))
            else:
                athena_type = Glue.type_pyarrow2athena(dtype)
                if name in partition_cols:
                    partition_cols_types[name] = athena_type
                else:
                    schema_built.append((name, athena_type))

        partition_cols_schema_built = [(name, partition_cols_types[name])
                                       for name in partition_cols]

        logger.debug(f"schema_built:\n{schema_built}")
        logger.debug(
            f"partition_cols_schema_built:\n{partition_cols_schema_built}")
        return schema_built, partition_cols_schema_built

    @staticmethod
    def parse_table_name(path):
        if path[-1] == "/":
            path = path[:-1]
        return path.rpartition("/")[2]

    @staticmethod
    def csv_table_definition(table, partition_cols_schema, schema, path,
                             compression, extra_args):
        if not partition_cols_schema:
            partition_cols_schema = []
        compressed = False if compression is None else True
        sep = extra_args["sep"] if "sep" in extra_args else ","
        serde = extra_args.get("serde")
        if serde == "OpenCSVSerDe":
            serde_fullname = "org.apache.hadoop.hive.serde2.OpenCSVSerde"
            param = {
                "separatorChar": sep,
                "quoteChar": "\"",
                "escapeChar": "\\",
            }
            refined_par_schema = [(name, "string")
                                  for name, dtype in partition_cols_schema]
            refined_schema = [(name, "string") for name, dtype in schema]
        elif serde == "LazySimpleSerDe":
            serde_fullname = "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"
            param = {"field.delim": sep, "escape.delim": "\\"}
            dtypes_allowed = ["int", "bigint", "float", "double"]
            refined_par_schema = [(name, dtype) if dtype in dtypes_allowed else
                                  (name, "string")
                                  for name, dtype in partition_cols_schema]
            refined_schema = [(name, dtype) if dtype in dtypes_allowed else
                              (name, "string") for name, dtype in schema]
        else:
            raise InvalidSerDe(f"{serde} in not in the valid SerDe list.")
        return {
            "Name":
            table,
            "PartitionKeys": [{
                "Name": x[0],
                "Type": x[1]
            } for x in refined_par_schema],
            "TableType":
            "EXTERNAL_TABLE",
            "Parameters": {
                "classification": "csv",
                "compressionType": str(compression).lower(),
                "typeOfData": "file",
                "delimiter": sep,
                "columnsOrdered": "true",
                "areColumnsQuoted": "false",
            },
            "StorageDescriptor": {
                "Columns": [{
                    "Name": x[0],
                    "Type": x[1]
                } for x in refined_schema],
                "Location": path,
                "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
                "OutputFormat":
                "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                "Compressed": compressed,
                "NumberOfBuckets": -1,
                "SerdeInfo": {
                    "Parameters": param,
                    "SerializationLibrary": serde_fullname,
                },
                "StoredAsSubDirectories": False,
                "SortColumns": [],
                "Parameters": {
                    "classification": "csv",
                    "compressionType": str(compression).lower(),
                    "typeOfData": "file",
                    "delimiter": sep,
                    "columnsOrdered": "true",
                    "areColumnsQuoted": "false",
                },
            },
        }

    @staticmethod
    def csv_partition_definition(partition, compression, extra_args):
        compressed = False if compression is None else True
        sep = extra_args["sep"] if "sep" in extra_args else ","
        serde = extra_args.get("serde")
        if serde == "OpenCSVSerDe":
            serde_fullname = "org.apache.hadoop.hive.serde2.OpenCSVSerde"
            param = {
                "separatorChar": sep,
                "quoteChar": "\"",
                "escapeChar": "\\",
            }
        elif serde == "LazySimpleSerDe":
            serde_fullname = "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"
            param = {"field.delim": sep, "escape.delim": "\\"}
        else:
            raise InvalidSerDe(f"{serde} in not in the valid SerDe list.")
        return {
            "StorageDescriptor": {
                "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
                "Location": partition[0],
                "Compressed": compressed,
                "SerdeInfo": {
                    "Parameters": param,
                    "SerializationLibrary": serde_fullname,
                },
                "StoredAsSubDirectories": False,
            },
            "Values": partition[1],
        }

    @staticmethod
    def parquet_table_definition(table, partition_cols_schema, schema, path,
                                 compression):
        if not partition_cols_schema:
            partition_cols_schema = []
        compressed = False if compression is None else True
        return {
            "Name":
            table,
            "PartitionKeys": [{
                "Name": x[0],
                "Type": x[1]
            } for x in partition_cols_schema],
            "TableType":
            "EXTERNAL_TABLE",
            "Parameters": {
                "classification": "parquet",
                "compressionType": str(compression).lower(),
                "typeOfData": "file",
            },
            "StorageDescriptor": {
                "Columns": [{
                    "Name": x[0],
                    "Type": x[1]
                } for x in schema],
                "Location": path,
                "InputFormat":
                "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                "OutputFormat":
                "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                "Compressed": compressed,
                "NumberOfBuckets": -1,
                "SerdeInfo": {
                    "SerializationLibrary":
                    "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
                    "Parameters": {
                        "serialization.format": "1"
                    },
                },
                "StoredAsSubDirectories": False,
                "SortColumns": [],
                "Parameters": {
                    "CrawlerSchemaDeserializerVersion": "1.0",
                    "classification": "parquet",
                    "compressionType": str(compression).lower(),
                    "typeOfData": "file",
                },
            },
        }

    @staticmethod
    def parquet_partition_definition(partition, compression):
        compressed = False if compression is None else True
        return {
            "StorageDescriptor": {
                "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
                "Location": partition[0],
                "Compressed": compressed,
                "SerdeInfo": {
                    "Parameters": {
                        "serialization.format": "1"
                    },
                    "SerializationLibrary":
                    "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
                },
                "StoredAsSubDirectories": False,
            },
            "Values": partition[1],
        }

    @staticmethod
    def _parse_partitions_tuples(objects_paths, partition_cols):
        paths = {f"{path.rpartition('/')[0]}/" for path in objects_paths}
        return [(
            path,
            Glue._parse_partition_values(path=path,
                                         partition_cols=partition_cols),
        ) for path in paths]

    @staticmethod
    def _parse_partition_values(path, partition_cols):
        return [
            re.search(f"/{col}=(.*?)/", path).group(1)
            for col in partition_cols
        ]
