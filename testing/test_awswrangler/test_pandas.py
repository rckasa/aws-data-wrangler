from time import sleep
import logging
import csv
from datetime import datetime, date

import pytest
import boto3
import pandas
import numpy

from awswrangler import Session, Pandas
from awswrangler.exceptions import LineTerminatorNotFound, EmptyDataframe, InvalidSerDe

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(levelname)s][%(name)s][%(funcName)s] %(message)s")
logging.getLogger("awswrangler").setLevel(logging.DEBUG)


@pytest.fixture(scope="module")
def cloudformation_outputs():
    response = boto3.client("cloudformation").describe_stacks(
        StackName="aws-data-wrangler-test-arena")
    outputs = {}
    for output in response.get("Stacks")[0].get("Outputs"):
        outputs[output.get("OutputKey")] = output.get("OutputValue")
    yield outputs


@pytest.fixture(scope="module")
def session():
    yield Session()


@pytest.fixture(scope="module")
def bucket(session, cloudformation_outputs):
    if "BucketName" in cloudformation_outputs:
        bucket = cloudformation_outputs["BucketName"]
        session.s3.delete_objects(path=f"s3://{bucket}/")
    else:
        raise Exception(
            "You must deploy the test infrastructure using Cloudformation!")
    yield bucket
    session.s3.delete_objects(path=f"s3://{bucket}/")


@pytest.fixture(scope="module")
def database(cloudformation_outputs):
    if "GlueDatabaseName" in cloudformation_outputs:
        database = cloudformation_outputs["GlueDatabaseName"]
    else:
        raise Exception(
            "You must deploy the test infrastructure using Cloudformation!")
    yield database


@pytest.fixture(scope="module")
def kms_key(cloudformation_outputs):
    if "KmsKeyArn" in cloudformation_outputs:
        database = cloudformation_outputs["KmsKeyArn"]
    else:
        raise Exception(
            "You must deploy the test infrastructure using Cloudformation!")
    yield database


@pytest.fixture(scope="module")
def loggroup(cloudformation_outputs):
    if "LogGroupName" in cloudformation_outputs:
        database = cloudformation_outputs["LogGroupName"]
    else:
        raise Exception(
            "You must deploy the test infrastructure using Cloudformation!")
    yield database


@pytest.fixture(scope="module")
def logstream(cloudformation_outputs, loggroup):
    if "LogStream" in cloudformation_outputs:
        logstream = cloudformation_outputs["LogStream"]
    else:
        raise Exception(
            "You must deploy the test infrastructure using Cloudformation!")
    client = boto3.client("logs")
    response = client.describe_log_streams(logGroupName=loggroup,
                                           logStreamNamePrefix=logstream)
    token = response["logStreams"][0].get("uploadSequenceToken")
    events = []
    for i in range(5):
        events.append({
            "timestamp": int(1000 * datetime.utcnow().timestamp()),
            "message": str(i)
        })
    args = {
        "logGroupName": loggroup,
        "logStreamName": logstream,
        "logEvents": events
    }
    if token:
        args["sequenceToken"] = token
    client.put_log_events(**args)
    sleep(300)
    yield logstream


@pytest.mark.parametrize("sample, row_num", [("data_samples/micro.csv", 30),
                                             ("data_samples/small.csv", 100)])
def test_read_csv(session, bucket, sample, row_num):
    boto3.client("s3").upload_file(sample, bucket, sample)
    path = f"s3://{bucket}/{sample}"
    dataframe = session.pandas.read_csv(path=path)
    session.s3.delete_objects(path=path)
    assert len(dataframe.index) == row_num


@pytest.mark.parametrize("sample, row_num", [("data_samples/micro.csv", 30),
                                             ("data_samples/small.csv", 100)])
def test_read_csv_iterator(session, bucket, sample, row_num):
    boto3.client("s3").upload_file(sample, bucket, sample)
    path = f"s3://{bucket}/{sample}"
    dataframe_iter = session.pandas.read_csv(path=path, max_result_size=200)
    total_count = 0
    for dataframe in dataframe_iter:
        total_count += len(dataframe.index)
    session.s3.delete_objects(path=path)
    assert total_count == row_num


@pytest.mark.parametrize(
    "mode, file_format, preserve_index, partition_cols, procs_cpu_bound, factor",
    [
        ("overwrite", "csv", False, [], 1, 1),
        ("append", "csv", False, [], 1, 2),
        ("overwrite_partitions", "csv", False, [], 1, 1),
        ("overwrite", "csv", True, [], 1, 1),
        ("append", "csv", True, [], 1, 2),
        ("overwrite_partitions", "csv", True, [], 1, 1),
        ("overwrite", "csv", False, [], 5, 1),
        ("append", "csv", False, [], 5, 2),
        ("overwrite_partitions", "csv", False, [], 5, 1),
        ("overwrite", "csv", True, [], 5, 1),
        ("append", "csv", True, [], 5, 2),
        ("overwrite_partitions", "csv", True, [], 5, 1),
        ("overwrite", "csv", False, ["date"], 1, 1),
        ("append", "csv", False, ["date"], 1, 2),
        ("overwrite_partitions", "csv", False, ["date"], 1, 1),
        ("overwrite", "csv", True, ["date"], 1, 1),
        ("append", "csv", True, ["date"], 1, 2),
        ("overwrite_partitions", "csv", True, ["date"], 1, 1),
        ("overwrite", "csv", False, ["date"], 5, 1),
        ("append", "csv", False, ["date"], 5, 2),
        ("overwrite_partitions", "csv", False, ["date"], 5, 1),
        ("overwrite", "csv", True, ["date"], 5, 1),
        ("append", "csv", True, ["date"], 5, 2),
        ("overwrite_partitions", "csv", True, ["date"], 5, 1),
        ("overwrite", "csv", False, ["name", "date"], 1, 1),
        ("append", "csv", False, ["name", "date"], 1, 2),
        ("overwrite_partitions", "csv", False, ["name", "date"], 1, 1),
        ("overwrite", "csv", True, ["name", "date"], 1, 1),
        ("append", "csv", True, ["name", "date"], 1, 2),
        ("overwrite_partitions", "csv", True, ["name", "date"], 1, 1),
        ("overwrite", "csv", False, ["name", "date"], 5, 1),
        ("append", "csv", False, ["name", "date"], 5, 2),
        ("overwrite_partitions", "csv", False, ["name", "date"], 5, 1),
        ("overwrite", "csv", True, ["name", "date"], 5, 1),
        ("append", "csv", True, ["name", "date"], 5, 2),
        ("overwrite_partitions", "csv", True, ["name", "date"], 2, 1),
        ("overwrite", "parquet", False, [], 1, 1),
        ("append", "parquet", False, [], 1, 2),
        ("overwrite_partitions", "parquet", False, [], 1, 1),
        ("overwrite", "parquet", True, [], 1, 1),
        ("append", "parquet", True, [], 1, 2),
        ("overwrite_partitions", "parquet", True, [], 1, 1),
        ("overwrite", "parquet", False, [], 5, 1),
        ("append", "parquet", False, [], 5, 2),
        ("overwrite_partitions", "parquet", False, [], 5, 1),
        ("overwrite", "parquet", True, [], 5, 1),
        ("append", "parquet", True, [], 5, 2),
        ("overwrite_partitions", "parquet", True, [], 5, 1),
        ("overwrite", "parquet", False, ["date"], 1, 1),
        ("append", "parquet", False, ["date"], 1, 2),
        ("overwrite_partitions", "parquet", False, ["date"], 1, 1),
        ("overwrite", "parquet", True, ["date"], 1, 1),
        ("append", "parquet", True, ["date"], 1, 2),
        ("overwrite_partitions", "parquet", True, ["date"], 1, 1),
        ("overwrite", "parquet", False, ["date"], 5, 1),
        ("append", "parquet", False, ["date"], 5, 2),
        ("overwrite_partitions", "parquet", False, ["date"], 5, 1),
        ("overwrite", "parquet", True, ["date"], 5, 1),
        ("append", "parquet", True, ["date"], 5, 2),
        ("overwrite_partitions", "parquet", True, ["date"], 5, 1),
        ("overwrite", "parquet", False, ["name", "date"], 1, 1),
        ("append", "parquet", False, ["name", "date"], 1, 2),
        ("overwrite_partitions", "parquet", False, ["name", "date"], 1, 1),
        ("overwrite", "parquet", True, ["name", "date"], 1, 1),
        ("append", "parquet", True, ["name", "date"], 1, 2),
        ("overwrite_partitions", "parquet", True, ["name", "date"], 1, 1),
        ("overwrite", "parquet", False, ["name", "date"], 5, 1),
        ("append", "parquet", False, ["name", "date"], 5, 2),
        ("overwrite_partitions", "parquet", False, ["name", "date"], 5, 1),
        ("overwrite", "parquet", True, ["name", "date"], 5, 1),
        ("append", "parquet", True, ["name", "date"], 5, 2),
        ("overwrite_partitions", "parquet", True, ["name", "date"], 5, 1),
    ],
)
def test_to_s3(
        session,
        bucket,
        database,
        mode,
        file_format,
        preserve_index,
        partition_cols,
        procs_cpu_bound,
        factor,
):
    dataframe = pandas.read_csv("data_samples/micro.csv")
    func = session.pandas.to_csv if file_format == "csv" else session.pandas.to_parquet
    objects_paths = func(
        dataframe=dataframe,
        database=database,
        path=f"s3://{bucket}/test/",
        preserve_index=preserve_index,
        mode=mode,
        partition_cols=partition_cols,
        procs_cpu_bound=procs_cpu_bound,
    )
    num_partitions = (len([keys for keys in dataframe.groupby(partition_cols)])
                      if partition_cols else 1)
    assert len(objects_paths) >= num_partitions
    dataframe2 = None
    for counter in range(10):
        dataframe2 = session.pandas.read_sql_athena(sql="select * from test",
                                                    database=database)
        if factor * len(dataframe.index) == len(dataframe2.index):
            break
        sleep(1)
    assert factor * len(dataframe.index) == len(dataframe2.index)
    if preserve_index:
        assert (len(list(dataframe.columns)) + 1) == len(
            list(dataframe2.columns))
    else:
        assert len(list(dataframe.columns)) == len(list(dataframe2.columns))


def test_to_parquet_with_cast_int(
        session,
        bucket,
        database,
):
    dataframe = pandas.read_csv("data_samples/nano.csv",
                                dtype={"id": "Int64"},
                                parse_dates=["date", "time"])
    session.pandas.to_parquet(dataframe=dataframe,
                              database=database,
                              path=f"s3://{bucket}/test/",
                              preserve_index=False,
                              mode="overwrite",
                              procs_cpu_bound=1,
                              cast_columns={"value": "int"})
    dataframe2 = None
    for counter in range(10):
        dataframe2 = session.pandas.read_sql_athena(sql="select * from test",
                                                    database=database)
        if len(dataframe.index) == len(dataframe2.index):
            break
        sleep(2)
    assert len(dataframe.index) == len(dataframe2.index)
    assert len(list(dataframe.columns)) == len(list(dataframe2.columns))
    assert dataframe[dataframe["id"] == 0].iloc[0]["name"] == dataframe2[
        dataframe2["id"] == 0].iloc[0]["name"]


@pytest.mark.parametrize("sample, row_num, max_result_size", [
    ("data_samples/nano.csv", 5, 5000),
    ("data_samples/micro.csv", 30, 100),
    ("data_samples/small.csv", 100, 100),
    ("data_samples/micro.csv", 30, 500),
    ("data_samples/small.csv", 100, 500),
    ("data_samples/micro.csv", 30, 3000),
    ("data_samples/small.csv", 100, 3000),
    ("data_samples/micro.csv", 30, 700),
])
def test_read_sql_athena_iterator(session, bucket, database, sample, row_num,
                                  max_result_size):
    parse_dates = []
    if sample == "data_samples/nano.csv":
        parse_dates.append("time")
        parse_dates.append("date")
    dataframe_sample = pandas.read_csv(sample, parse_dates=parse_dates)
    path = f"s3://{bucket}/test/"
    session.pandas.to_parquet(dataframe=dataframe_sample,
                              database=database,
                              path=path,
                              preserve_index=False,
                              mode="overwrite")
    total_count = 0
    for counter in range(10):
        dataframe_iter = session.pandas.read_sql_athena(
            sql="select * from test",
            database=database,
            max_result_size=max_result_size)
        total_count = 0
        for dataframe in dataframe_iter:
            total_count += len(dataframe.index)
            assert len(list(dataframe.columns)) == len(
                list(dataframe_sample.columns))
            print(dataframe)
        if total_count == row_num:
            break
        sleep(1)
    session.s3.delete_objects(path=path)
    assert total_count == row_num


@pytest.mark.parametrize(
    "body, sep, quotechar, lineterminator, last_index, last_terminator_suspect_index,"
    "first_non_special_byte_index, sep_counter, quote_counter", [
        (b'"foo","boo"\n', ",", '"', "\n", None, 11, 9, 0, 1),
        (b'"foo","boo"\n"bar', ",", '"', "\n", None, 11, 9, 0, 1),
        (b'!foo!;!boo!@', ";", '!', "@", None, 11, 9, 0, 1),
        (b'"foo","boo"\n"bar\n', ",", '"', "\n", 16, 11, 9, 0, 1),
    ])
def test_extract_terminator_profile(body, sep, quotechar, lineterminator,
                                    last_index, last_terminator_suspect_index,
                                    first_non_special_byte_index, sep_counter,
                                    quote_counter):
    profile = Pandas._extract_terminator_profile(body=body,
                                                 sep=sep,
                                                 quotechar=quotechar,
                                                 lineterminator=lineterminator,
                                                 last_index=last_index)
    assert profile[
        "last_terminator_suspect_index"] == last_terminator_suspect_index
    assert profile[
        "first_non_special_byte_index"] == first_non_special_byte_index
    assert profile["sep_counter"] == sep_counter
    assert profile["quote_counter"] == quote_counter


@pytest.mark.parametrize(
    "body, sep, quoting, quotechar, lineterminator, ret", [
        (b"012\njawdnkjawnd", ",", csv.QUOTE_MINIMAL, '"', "\n", 3),
        (b"012\n456\njawdnkjawnd", ",", csv.QUOTE_MINIMAL, '"', "\n", 7),
        (b'012",\n"foo', ",", csv.QUOTE_ALL, '"', "\n", 5),
        (b'012",\n', ",", csv.QUOTE_ALL, '"', "\n", 5),
        (b'012",\n"012,\n', ",", csv.QUOTE_ALL, '"', "\n", 5),
        (b'012",\n,,,,,,,,"012,\n', ",", csv.QUOTE_ALL, '"', "\n", 5),
        (b'012",,,,\n"012,\n', ",", csv.QUOTE_ALL, '"', "\n", 8),
        (b'012",,,,\n,,,,,,""012,\n', ",", csv.QUOTE_ALL, '"', "\n", 8),
        (b'012",,,,\n,,,,,,""012"\n,', ",", csv.QUOTE_ALL, '"', "\n", 21),
        (b'012",,,,\n,,,,,,""01"2""\n,"a', ",", csv.QUOTE_ALL, '"', "\n", 8),
        (b'"foo","boo"\n"\n","bar"', ",", csv.QUOTE_ALL, '"', "\n", 11),
        (b'"foo"\n"boo","\n","\n","\n","\n","\n",,,,,,"\n",,,,', ",",
         csv.QUOTE_ALL, '"', "\n", 5),
        (b'012",\n"foo","\n\n\n\n","\n', ",", csv.QUOTE_ALL, '"', "\n", 5),
    ])
def test_find_terminator(body, sep, quoting, quotechar, lineterminator, ret):
    assert Pandas._find_terminator(body=body,
                                   sep=sep,
                                   quoting=quoting,
                                   quotechar=quotechar,
                                   lineterminator=lineterminator) == ret


@pytest.mark.parametrize(
    "body, sep, quoting, quotechar, lineterminator",
    [(b"jawdnkjawnd", ",", csv.QUOTE_MINIMAL, '"', "\n"),
     (b"jawdnkjawnd", ",", csv.QUOTE_ALL, '"', "\n"),
     (b"jawdnkj\nawnd", ",", csv.QUOTE_ALL, '"', "\n"),
     (b'jawdnkj"x\n\n"awnd', ",", csv.QUOTE_ALL, '"', "\n"),
     (b'jawdnkj""\n,,,,,,,,,,awnd', ",", csv.QUOTE_ALL, '"', "\n"),
     (b'jawdnkj,""""""\nawnd', ",", csv.QUOTE_ALL, '"', "\n")])
def test_find_terminator_exception(body, sep, quoting, quotechar,
                                   lineterminator):
    with pytest.raises(LineTerminatorNotFound):
        assert Pandas._find_terminator(body=body,
                                       sep=sep,
                                       quoting=quoting,
                                       quotechar=quotechar,
                                       lineterminator=lineterminator)


@pytest.mark.parametrize("max_result_size", [400, 700, 1000, 10000])
def test_etl_complex(session, bucket, database, max_result_size):
    dataframe = pandas.read_csv("data_samples/complex.csv",
                                dtype={"my_int_with_null": "Int64"},
                                parse_dates=["my_timestamp", "my_date"])
    session.pandas.to_parquet(dataframe=dataframe,
                              database=database,
                              path=f"s3://{bucket}/test/",
                              preserve_index=False,
                              mode="overwrite",
                              procs_cpu_bound=1)
    sleep(1)
    df_iter = session.pandas.read_sql_athena(sql="select * from test",
                                             database=database,
                                             max_result_size=max_result_size)
    count = 0
    for df in df_iter:
        count += len(df.index)
        for row in df.itertuples():
            assert len(list(dataframe.columns)) == len(list(df.columns))
            assert isinstance(row.my_timestamp, datetime)
            assert isinstance(row.my_date, date)
            assert isinstance(row.my_float, float)
            assert isinstance(row.my_int, numpy.int64)
            assert isinstance(row.my_string, str)
            assert str(row.my_timestamp) == "2018-01-01 04:03:02.001000"
            assert str(row.my_date) == "2019-02-02 00:00:00"
            assert str(row.my_float) == "12345.6789"
            assert str(row.my_int) == "123456789"
            assert str(
                row.my_string
            ) == "foo\nboo\nbar\nFOO\nBOO\nBAR\nxxxxx\nÁÃÀÂÇ\n汉字汉字汉字汉字汉字汉字汉字æøåæøåæøåæøåæøåæøåæøåæøåæøåæøå汉字汉字汉字汉字汉字汉字汉字æøåæøåæøåæøåæøåæøåæøåæøåæøåæøå"
    assert count == len(dataframe.index)


def test_to_parquet_with_kms(
        bucket,
        database,
        kms_key,
):
    extra_args = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": kms_key}
    session_inner = Session(s3_additional_kwargs=extra_args)
    dataframe = pandas.read_csv("data_samples/nano.csv")
    session_inner.pandas.to_parquet(dataframe=dataframe,
                                    database=database,
                                    path=f"s3://{bucket}/test/",
                                    preserve_index=False,
                                    mode="overwrite",
                                    procs_cpu_bound=1)
    dataframe2 = None
    for counter in range(10):
        dataframe2 = session_inner.pandas.read_sql_athena(
            sql="select * from test", database=database)
        if len(dataframe.index) == len(dataframe2.index):
            break
        sleep(1)
    assert len(dataframe.index) == len(dataframe2.index)
    assert len(list(dataframe.columns)) == len(list(dataframe2.columns))
    assert dataframe[dataframe["id"] == 0].iloc[0]["name"] == dataframe2[
        dataframe2["id"] == 0].iloc[0]["name"]


def test_to_parquet_with_empty_dataframe(session, bucket, database):
    dataframe = pandas.DataFrame()
    with pytest.raises(EmptyDataframe):
        assert session.pandas.to_parquet(dataframe=dataframe,
                                         database=database,
                                         path=f"s3://{bucket}/test/",
                                         preserve_index=False,
                                         mode="overwrite",
                                         procs_cpu_bound=1)


def test_read_log_query(session, loggroup, logstream):
    dataframe = session.pandas.read_log_query(
        log_group_names=[loggroup],
        query="fields @timestamp, @message | sort @timestamp desc | limit 5",
    )
    assert len(dataframe.index) == 5
    assert len(dataframe.columns) == 3


@pytest.mark.parametrize(
    "file_format, serde, index, partition_cols",
    [("csv", "OpenCSVSerDe", None, []), ("csv", "OpenCSVSerDe", "default", []),
     ("csv", "OpenCSVSerDe", "my_date", []),
     ("csv", "OpenCSVSerDe", "my_timestamp", []),
     ("csv", "OpenCSVSerDe", "my_timestamp", []),
     ("csv", "LazySimpleSerDe", "my_date", ["my_timestamp", "my_float"]),
     ("csv", "LazySimpleSerDe", None, []),
     ("csv", "LazySimpleSerDe", "default", []),
     ("csv", "LazySimpleSerDe", "my_date", []),
     ("csv", "LazySimpleSerDe", "my_timestamp", []),
     ("csv", "LazySimpleSerDe", "my_timestamp", ["my_date", "my_int"]),
     ("parquet", None, None, []), ("parquet", None, "default", []),
     ("parquet", None, "my_date", []), ("parquet", None, "my_timestamp", []),
     ("parquet", None, None, ["my_int"]),
     ("parquet", None, "default", ["my_int"]),
     ("parquet", None, "my_date", ["my_int"]),
     ("parquet", None, "my_timestamp", ["my_int"]),
     ("parquet", None, None, ["my_float"]),
     ("parquet", None, "default", ["my_float"]),
     ("parquet", None, "my_date", ["my_float"]),
     ("parquet", None, "my_timestamp", ["my_float"]),
     ("parquet", None, None, ["my_date"]),
     ("parquet", None, "default", ["my_date"]),
     ("parquet", None, "my_date", ["my_date"]),
     ("parquet", None, "my_timestamp", ["my_date"]),
     ("parquet", None, None, ["my_timestamp"]),
     ("parquet", None, "default", ["my_timestamp"]),
     ("parquet", None, "my_date", ["my_timestamp"]),
     ("parquet", None, "my_timestamp", ["my_timestamp"]),
     ("parquet", None, None, ["my_timestamp", "my_date"]),
     ("parquet", None, "default", ["my_date", "my_timestamp"]),
     ("parquet", None, "my_date", ["my_timestamp", "my_date"]),
     ("parquet", None, "my_timestamp", ["my_date", "my_timestamp"]),
     ("parquet", None, "default", ["my_date", "my_timestamp", "my_int"]),
     ("parquet", None, "my_date", ["my_timestamp", "my_float", "my_date"])])
def test_to_s3_types(session, bucket, database, file_format, serde, index,
                     partition_cols):
    dataframe = pandas.read_csv("data_samples/complex.csv",
                                dtype={"my_int_with_null": "Int64"},
                                parse_dates=["my_timestamp", "my_date"])
    dataframe["my_date"] = dataframe["my_date"].dt.date
    dataframe["my_bool"] = True

    preserve_index = True
    if not index:
        preserve_index = False
    elif index != "default":
        dataframe["new_index"] = dataframe[index]
        dataframe = dataframe.set_index("new_index")

    args = {
        "dataframe": dataframe,
        "database": database,
        "path": f"s3://{bucket}/test/",
        "preserve_index": preserve_index,
        "mode": "overwrite",
        "procs_cpu_bound": 1,
        "partition_cols": partition_cols
    }

    if file_format == "csv":
        func = session.pandas.to_csv
        args["serde"] = serde
        del dataframe["my_string"]
    else:
        func = session.pandas.to_parquet
    objects_paths = func(**args)
    assert len(objects_paths) == 1
    sleep(2)
    dataframe2 = session.pandas.read_sql_athena(sql="select * from test",
                                                database=database)
    for row in dataframe2.itertuples():

        if file_format == "parquet":
            if index:
                if index == "my_date":
                    assert isinstance(row.new_index, date)
                elif index == "my_timestamp":
                    assert isinstance(row.new_index, datetime)
            assert isinstance(row.my_timestamp, datetime)
            assert type(row.my_date) == date
            assert isinstance(row.my_float, float)
            assert isinstance(row.my_int, numpy.int64)
            assert isinstance(row.my_string, str)
            assert isinstance(row.my_bool, bool)
            assert str(
                row.my_string
            ) == "foo\nboo\nbar\nFOO\nBOO\nBAR\nxxxxx\nÁÃÀÂÇ\n汉字汉字汉字汉字汉字汉字汉字æøåæøåæøåæøåæøåæøåæøåæøåæøåæøå汉字汉字汉字汉字汉字汉字汉字æøåæøåæøåæøåæøåæøåæøåæøåæøåæøå"
        elif file_format == "csv":
            if serde == "LazySimpleSerDe":
                assert isinstance(row.my_float, float)
                assert isinstance(row.my_int, numpy.int64)
        assert str(row.my_timestamp).startswith("2018-01-01 04:03:02.001")
        assert str(row.my_date) == "2019-02-02"
        assert str(row.my_float) == "12345.6789"
        assert str(row.my_int) == "123456789"
        assert str(row.my_bool) == "True"

    assert len(dataframe.index) == len(dataframe2.index)
    if index:
        assert (len(list(dataframe.columns)) + 1) == len(
            list(dataframe2.columns))
    else:
        assert len(list(dataframe.columns)) == len(list(dataframe2.columns))


def test_to_csv_with_sep(
        session,
        bucket,
        database,
):
    dataframe = pandas.read_csv("data_samples/nano.csv")
    session.pandas.to_csv(dataframe=dataframe,
                          database=database,
                          path=f"s3://{bucket}/test/",
                          preserve_index=False,
                          mode="overwrite",
                          sep="|")
    dataframe2 = None
    for counter in range(10):
        dataframe2 = session.pandas.read_sql_athena(sql="select * from test",
                                                    database=database)
        if len(dataframe.index) == len(dataframe2.index):
            break
        sleep(2)
    assert len(dataframe.index) == len(dataframe2.index)
    assert len(list(dataframe.columns)) == len(list(dataframe2.columns))


def test_to_csv_serde_exception(
        session,
        bucket,
        database,
):
    dataframe = pandas.read_csv("data_samples/nano.csv")
    with pytest.raises(InvalidSerDe):
        assert session.pandas.to_csv(dataframe=dataframe,
                                     database=database,
                                     path=f"s3://{bucket}/test/",
                                     preserve_index=False,
                                     mode="overwrite",
                                     serde="foo")


@pytest.mark.parametrize("compression", [None, "snappy", "gzip"])
def test_to_parquet_compressed(session, bucket, database, compression):
    dataframe = pandas.read_csv("data_samples/small.csv")
    session.pandas.to_parquet(dataframe=dataframe,
                              database=database,
                              path=f"s3://{bucket}/test/",
                              preserve_index=False,
                              mode="overwrite",
                              compression=compression,
                              procs_cpu_bound=1)
    dataframe2 = None
    for counter in range(10):
        dataframe2 = session.pandas.read_sql_athena(sql="select * from test",
                                                    database=database)
        if len(dataframe.index) == len(dataframe2.index):
            break
        sleep(2)
    assert len(dataframe.index) == len(dataframe2.index)
    assert len(list(dataframe.columns)) == len(list(dataframe2.columns))
    assert dataframe[dataframe["id"] == 1].iloc[0]["name"] == dataframe2[
        dataframe2["id"] == 1].iloc[0]["name"]


def test_to_parquet_lists(session, bucket, database):
    dataframe = pandas.DataFrame({
        "id": [0, 1],
        "col_int": [[1, 2], [3, 4, 5]],
        "col_float": [[1.0, 2.0, 3.0], [4.0, 5.0]],
        "col_string": [["foo"], ["boo", "bar"]],
        "col_timestamp": [[datetime(2019, 1, 1),
                           datetime(2019, 1, 2)], [datetime(2019, 1, 3)]],
        "col_date": [[date(2019, 1, 1), date(2019, 1, 2)], [date(2019, 1, 3)]],
        "col_list_int": [[[1]], [[2, 3], [4, 5, 6]]],
        "col_list_list_string": [[[["foo"]]], [[["boo", "bar"]]]],
    })
    paths = session.pandas.to_parquet(dataframe=dataframe,
                                      database=database,
                                      path=f"s3://{bucket}/test/",
                                      preserve_index=False,
                                      mode="overwrite",
                                      procs_cpu_bound=1)
    assert len(paths) == 1
    dataframe2 = None
    for counter in range(10):
        dataframe2 = session.pandas.read_sql_athena(
            sql="select id, col_int, col_float, col_list_int from test",
            database=database)
        if len(dataframe.index) == len(dataframe2.index):
            break
        sleep(2)
    assert len(dataframe.index) == len(dataframe2.index)
    assert 4 == len(list(dataframe2.columns))
    val = dataframe[dataframe["id"] == 0].iloc[0]["col_list_int"]
    val2 = dataframe2[dataframe2["id"] == 0].iloc[0]["col_list_int"]
    assert val == val2


def test_to_parquet_cast(session, bucket, database):
    dataframe = pandas.DataFrame({
        "id": [0, 1],
        "col_int": [[1, 2], [3, 4, 5]],
        "col_float": [[1.0, 2.0, 3.0], [4.0, 5.0]],
        "col_string": [["foo"], ["boo", "bar"]],
        "col_timestamp": [[datetime(2019, 1, 1),
                           datetime(2019, 1, 2)], [datetime(2019, 1, 3)]],
        "col_date": [[date(2019, 1, 1), date(2019, 1, 2)], [date(2019, 1, 3)]],
        "col_list_int": [[[1]], [[2, 3], [4, 5, 6]]],
        "col_list_list_string": [[[["foo"]]], [[["boo", "bar"]]]],
    })
    paths = session.pandas.to_parquet(dataframe=dataframe,
                                      database=database,
                                      path=f"s3://{bucket}/test/",
                                      preserve_index=False,
                                      mode="overwrite",
                                      procs_cpu_bound=1)
    assert len(paths) == 1
    dataframe2 = None
    for counter in range(10):
        dataframe2 = session.pandas.read_sql_athena(
            sql="select id, col_int, col_float, col_list_int from test",
            database=database)
        if len(dataframe.index) == len(dataframe2.index):
            break
        sleep(2)
    assert len(dataframe.index) == len(dataframe2.index)
    assert 4 == len(list(dataframe2.columns))
    val = dataframe[dataframe["id"] == 0].iloc[0]["col_list_int"]
    val2 = dataframe2[dataframe2["id"] == 0].iloc[0]["col_list_int"]
    assert val == val2


def test_to_parquet_with_cast_null(
        session,
        bucket,
        database,
):
    dataframe = pandas.DataFrame({
        "id": [0, 1],
        "col_null_tinyint": [None, None],
        "col_null_smallint": [None, None],
        "col_null_int": [None, None],
        "col_null_bigint": [None, None],
        "col_null_float": [None, None],
        "col_null_double": [None, None],
        "col_null_string": [None, None],
        "col_null_date": [None, None],
        "col_null_timestamp": [None, None],
    })
    session.pandas.to_parquet(dataframe=dataframe,
                              database=database,
                              path=f"s3://{bucket}/test/",
                              preserve_index=False,
                              mode="overwrite",
                              procs_cpu_bound=1,
                              cast_columns={
                                  "col_null_tinyint": "tinyint",
                                  "col_null_smallint": "smallint",
                                  "col_null_int": "int",
                                  "col_null_bigint": "bigint",
                                  "col_null_float": "float",
                                  "col_null_double": "double",
                                  "col_null_string": "string",
                                  "col_null_date": "date",
                                  "col_null_timestamp": "timestamp",
                              })
    dataframe2 = None
    for counter in range(10):
        dataframe2 = session.pandas.read_sql_athena(sql="select * from test",
                                                    database=database)
        if len(dataframe.index) == len(dataframe2.index):
            break
        sleep(2)
    assert len(dataframe.index) == len(dataframe2.index)
    assert len(list(dataframe.columns)) == len(list(dataframe2.columns))
