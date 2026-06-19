"""
Shared ADLS connection helper.
All three pipeline stages (bronze, silver, gold) import this -
one connection definition, reused everywhere, not copy-pasted.
"""

import os
from azure.storage.filedatalake import DataLakeServiceClient


def get_service_client() -> DataLakeServiceClient:
    storage_account_name = os.environ.get("STORAGE_ACCOUNT_NAME")
    storage_account_key = os.environ.get("STORAGE_ACCOUNT_KEY")

    if not storage_account_name or not storage_account_key:
        raise ValueError(
            "Missing required environment variables: "
            "STORAGE_ACCOUNT_NAME, STORAGE_ACCOUNT_KEY"
        )

    return DataLakeServiceClient(
        account_url=f"https://{storage_account_name}.dfs.core.windows.net",
        credential=storage_account_key
    )


def write_json_to_adls(service_client, container: str, path: str, payload: str):
    """Write a JSON string payload to a given ADLS container/path."""
    fs_client = service_client.get_file_system_client(file_system=container)
    file_client = fs_client.get_file_client(path)
    file_client.upload_data(payload.encode("utf-8"), overwrite=True)
    print(f"Landed at: {container}/{path}")


def read_json_from_adls(service_client, container: str, path: str) -> str:
    """Read a JSON string payload from a given ADLS container/path."""
    fs_client = service_client.get_file_system_client(file_system=container)
    file_client = fs_client.get_file_client(path)
    download = file_client.download_file()
    return download.readall().decode("utf-8")


def get_latest_file_path(service_client, container: str, prefix: str) -> str:
    """
    Find the most recently written file under a given prefix/folder
    in a container. Used so silver.py can find bronze's latest output
    without bronze.py needing to pass the filename directly.
    """
    fs_client = service_client.get_file_system_client(file_system=container)
    paths = list(fs_client.get_paths(path=prefix))
    files = [p for p in paths if not p.is_directory]
    if not files:
        raise FileNotFoundError(f"No files found under {container}/{prefix}")
    latest = max(files, key=lambda p: p.last_modified)
    return latest.name
