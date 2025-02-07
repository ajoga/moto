import boto3
from botocore.client import ClientError
import pytest

from moto import mock_s3
from moto.s3.responses import DEFAULT_REGION_NAME


@mock_s3
def test_put_bucket_logging():
    s3_client = boto3.client("s3", region_name=DEFAULT_REGION_NAME)
    bucket_name = "mybucket"
    log_bucket = "logbucket"
    wrong_region_bucket = "wrongregionlogbucket"
    s3_client.create_bucket(Bucket=bucket_name)
    # Adding the ACL for log-delivery later...
    s3_client.create_bucket(Bucket=log_bucket)
    s3_client.create_bucket(
        Bucket=wrong_region_bucket,
        CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
    )

    # No logging config:
    result = s3_client.get_bucket_logging(Bucket=bucket_name)
    assert not result.get("LoggingEnabled")

    # A log-bucket that doesn't exist:
    with pytest.raises(ClientError) as err:
        s3_client.put_bucket_logging(
            Bucket=bucket_name,
            BucketLoggingStatus={
                "LoggingEnabled": {"TargetBucket": "IAMNOTREAL", "TargetPrefix": ""}
            },
        )
    assert err.value.response["Error"]["Code"] == "InvalidTargetBucketForLogging"

    # A log-bucket that's missing the proper ACLs for LogDelivery:
    with pytest.raises(ClientError) as err:
        s3_client.put_bucket_logging(
            Bucket=bucket_name,
            BucketLoggingStatus={
                "LoggingEnabled": {"TargetBucket": log_bucket, "TargetPrefix": ""}
            },
        )
    assert err.value.response["Error"]["Code"] == "InvalidTargetBucketForLogging"
    assert "log-delivery" in err.value.response["Error"]["Message"]

    # Add the proper "log-delivery" ACL to the log buckets:
    bucket_owner = s3_client.get_bucket_acl(Bucket=log_bucket)["Owner"]
    for bucket in [log_bucket, wrong_region_bucket]:
        s3_client.put_bucket_acl(
            Bucket=bucket,
            AccessControlPolicy={
                "Grants": [
                    {
                        "Grantee": {
                            "URI": "http://acs.amazonaws.com/groups/s3/LogDelivery",
                            "Type": "Group",
                        },
                        "Permission": "WRITE",
                    },
                    {
                        "Grantee": {
                            "URI": "http://acs.amazonaws.com/groups/s3/LogDelivery",
                            "Type": "Group",
                        },
                        "Permission": "READ_ACP",
                    },
                    {
                        "Grantee": {"Type": "CanonicalUser", "ID": bucket_owner["ID"]},
                        "Permission": "FULL_CONTROL",
                    },
                ],
                "Owner": bucket_owner,
            },
        )

    # A log-bucket that's in the wrong region:
    with pytest.raises(ClientError) as err:
        s3_client.put_bucket_logging(
            Bucket=bucket_name,
            BucketLoggingStatus={
                "LoggingEnabled": {
                    "TargetBucket": wrong_region_bucket,
                    "TargetPrefix": "",
                }
            },
        )
    assert err.value.response["Error"]["Code"] == "CrossLocationLoggingProhibitted"

    # Correct logging:
    s3_client.put_bucket_logging(
        Bucket=bucket_name,
        BucketLoggingStatus={
            "LoggingEnabled": {
                "TargetBucket": log_bucket,
                "TargetPrefix": f"{bucket_name}/",
            }
        },
    )
    result = s3_client.get_bucket_logging(Bucket=bucket_name)
    assert result["LoggingEnabled"]["TargetBucket"] == log_bucket
    assert result["LoggingEnabled"]["TargetPrefix"] == f"{bucket_name}/"
    assert not result["LoggingEnabled"].get("TargetGrants")

    # And disabling:
    s3_client.put_bucket_logging(Bucket=bucket_name, BucketLoggingStatus={})
    assert not s3_client.get_bucket_logging(Bucket=bucket_name).get("LoggingEnabled")

    # And enabling with multiple target grants:
    s3_client.put_bucket_logging(
        Bucket=bucket_name,
        BucketLoggingStatus={
            "LoggingEnabled": {
                "TargetBucket": log_bucket,
                "TargetPrefix": f"{bucket_name}/",
                "TargetGrants": [
                    {
                        "Grantee": {
                            "ID": "SOMEIDSTRINGHERE9238748923734823917498237489237409123840983274",
                            "Type": "CanonicalUser",
                        },
                        "Permission": "READ",
                    },
                    {
                        "Grantee": {
                            "ID": "SOMEIDSTRINGHERE9238748923734823917498237489237409123840983274",
                            "Type": "CanonicalUser",
                        },
                        "Permission": "WRITE",
                    },
                ],
            }
        },
    )

    result = s3_client.get_bucket_logging(Bucket=bucket_name)
    assert len(result["LoggingEnabled"]["TargetGrants"]) == 2
    assert (
        result["LoggingEnabled"]["TargetGrants"][0]["Grantee"]["ID"]
        == "SOMEIDSTRINGHERE9238748923734823917498237489237409123840983274"
    )

    # Test with just 1 grant:
    s3_client.put_bucket_logging(
        Bucket=bucket_name,
        BucketLoggingStatus={
            "LoggingEnabled": {
                "TargetBucket": log_bucket,
                "TargetPrefix": f"{bucket_name}/",
                "TargetGrants": [
                    {
                        "Grantee": {
                            "ID": "SOMEIDSTRINGHERE9238748923734823917498237489237409123840983274",
                            "Type": "CanonicalUser",
                        },
                        "Permission": "READ",
                    }
                ],
            }
        },
    )
    result = s3_client.get_bucket_logging(Bucket=bucket_name)
    assert len(result["LoggingEnabled"]["TargetGrants"]) == 1

    # With an invalid grant:
    with pytest.raises(ClientError) as err:
        s3_client.put_bucket_logging(
            Bucket=bucket_name,
            BucketLoggingStatus={
                "LoggingEnabled": {
                    "TargetBucket": log_bucket,
                    "TargetPrefix": f"{bucket_name}/",
                    "TargetGrants": [
                        {
                            "Grantee": {
                                "ID": (
                                    "SOMEIDSTRINGHERE9238748923734823917498"
                                    "237489237409123840983274"
                                ),
                                "Type": "CanonicalUser",
                            },
                            "Permission": "NOTAREALPERM",
                        }
                    ],
                }
            },
        )
    assert err.value.response["Error"]["Code"] == "MalformedXML"


@mock_s3
def test_log_file_is_created():
    # Create necessary buckets
    s3_client = boto3.client("s3", region_name=DEFAULT_REGION_NAME)
    bucket_name = "mybucket"
    log_bucket = "logbucket"
    s3_client.create_bucket(Bucket=bucket_name)
    s3_client.create_bucket(Bucket=log_bucket)

    # Enable logging
    bucket_owner = s3_client.get_bucket_acl(Bucket=log_bucket)["Owner"]
    s3_client.put_bucket_acl(
        Bucket=log_bucket,
        AccessControlPolicy={
            "Grants": [
                {
                    "Grantee": {
                        "URI": "http://acs.amazonaws.com/groups/s3/LogDelivery",
                        "Type": "Group",
                    },
                    "Permission": "WRITE",
                },
                {
                    "Grantee": {
                        "URI": "http://acs.amazonaws.com/groups/s3/LogDelivery",
                        "Type": "Group",
                    },
                    "Permission": "READ_ACP",
                },
                {
                    "Grantee": {"Type": "CanonicalUser", "ID": bucket_owner["ID"]},
                    "Permission": "FULL_CONTROL",
                },
            ],
            "Owner": bucket_owner,
        },
    )
    s3_client.put_bucket_logging(
        Bucket=bucket_name,
        BucketLoggingStatus={
            "LoggingEnabled": {
                "TargetBucket": log_bucket,
                "TargetPrefix": f"{bucket_name}/",
            }
        },
    )

    # Make some requests against the source bucket
    s3_client.put_object(Bucket=bucket_name, Key="key1", Body=b"")
    s3_client.put_object(Bucket=bucket_name, Key="key2", Body=b"data")

    s3_client.put_bucket_logging(
        Bucket=bucket_name,
        BucketLoggingStatus={
            "LoggingEnabled": {"TargetBucket": log_bucket, "TargetPrefix": ""}
        },
    )
    s3_client.list_objects_v2(Bucket=bucket_name)

    # Verify files are created in the target (logging) bucket
    keys = [k["Key"] for k in s3_client.list_objects_v2(Bucket=log_bucket)["Contents"]]
    assert len([k for k in keys if k.startswith("mybucket/")]) == 3
    assert len([k for k in keys if not k.startswith("mybucket/")]) == 1

    # Verify (roughly) files have the correct content
    contents = [
        s3_client.get_object(Bucket=log_bucket, Key=key)["Body"].read().decode("utf-8")
        for key in keys
    ]
    assert any(c for c in contents if bucket_name in c)
    assert any(c for c in contents if "REST.GET.BUCKET" in c)
    assert any(c for c in contents if "REST.PUT.BUCKET" in c)
