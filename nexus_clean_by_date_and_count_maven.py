import asyncio
import aiohttp
import logging
import os
import requests
import time
import yaml
from datetime import datetime


dry_run = "true"  # set it to false to delete components

today_datetime = datetime.now()

repository = os.environ.get("NEXUS_REPOSITORY", "error")

log_file_dir = "/var/log/nexus-cleanup"
today_date = datetime.today().strftime("%Y-%m-%d")
log_filename = f"{repository}-{'dry-run-' if dry_run == 'true' else ''}{today_date}.log"
log_file_path = os.path.join(log_file_dir, log_filename)

logger = logging.getLogger(__name__)
logging.basicConfig(filename=f"{log_file_path}", encoding="utf-8", level=logging.INFO)
logging.info(f"Start cleaning at {today_datetime}")
logging.info(f"dry-run: {dry_run}")

allowed_repositories = ["snapshots", "releases", "tests"]

if repository not in allowed_repositories:
    logging.error(
        f"Check if environment variable NEXUS_REPOSITORY set. Current value: {repository}"
    )
    print(f"Check logs: {log_file_path}")
    exit(1)

CONFIG_MAP = {"snapshots": (1, 3), "releases": (1, 30), "testing": (1, 14)}

remain_count, remain_days = CONFIG_MAP.get(repository, (None, None))

nexus_url = "https://nexus.mydomain.com/service/rest"

# read Nexus credentials from a YAML file
with open("credentials.yaml", "r") as stream:
    try:
        credentials = yaml.safe_load(stream)
    except (IOError, yaml.YAMLError) as e:
        logger.error(e)
        exit(1)

# set the base URL and authentication credentials
auth = (
    credentials.get("vars", {}).get("user"),
    credentials.get("vars", {}).get("password"),
)


async def fetch_assets(session, url):
    continuation_token = None
    assets = []
    while True:
        url_with_token = (
            f"{url}&continuationToken={continuation_token}"
            if continuation_token
            else url
        )
        async with session.get(url_with_token) as response:
            data = await response.json()
            assets.extend(data.get("items", []))
            continuation_token = data.get("continuationToken")
            if not continuation_token:
                break
    return assets


async def main():
    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(*auth)) as session:
        assets = await fetch_assets(
            session, f"{nexus_url}/v1/components?repository={repository}"
        )
        process_assets(assets)


def sort_key(obj):
    """
    Custom sorting key function. It based on field lastDownloaded when it exists, otherwise on the filed lastModified
    """
    last_downloaded = obj.get("lastDownloaded")
    if last_downloaded is not None:
        return datetime.fromisoformat(last_downloaded)
    else:
        return datetime.fromisoformat(obj["lastModified"])


def compare_versions(version1, version2):
    """
    Compare two version numbers and return the result of the comparison.
    """
    version1_parts = version1.split(".")
    version2_parts = version2.split(".")

    for i in range(max(len(version1_parts), len(version2_parts))):
        if i >= len(version1_parts):
            return -1  # version1 is considered smaller
        if i >= len(version2_parts):
            return 1  # version1 is considered larger

        if version1_parts[i] != version2_parts[i]:
            if version1_parts[i].isdigit() and version2_parts[i].isdigit():
                return int(version1_parts[i]) - int(version2_parts[i])
            else:
                return version1_parts[i].__lt__(version2_parts[i])

    return 0  # both versions are equal


def delete_empty_components(assets):
    """Clean components where assets is empty"""
    # counter to count components deleted
    empty_components_count = 0

    empty_components_to_be_deleted = [asset for asset in assets if not asset["assets"]]

    # delete components without assets (empty)
    if empty_components_to_be_deleted:
        logging.info("Deleting empty components")
        for asset in empty_components_to_be_deleted:
            empty_components_count += 1
            logging.info(f'Deleting empty component: {asset["name"]}')
            if dry_run == "true":
                logging.info(
                    f'DRY-RUN mode: requests.delete {nexus_url}/v1/components/{asset["id"]}'
                )
            else:
                x = requests.delete(
                    f'{nexus_url}/v1/components/{asset["id"]}', auth=auth
                )
                logging.info(f"Response code: {x.status_code}")

    return empty_components_count


def process_assets(assets):

    empty_components_count = delete_empty_components(assets)

    # determine names of apps for cleaning version
    components_to_be_deleted = []
    [components_to_be_deleted.append(i) for i in assets if i["assets"]]

    # determine names of apps
    asset_all_names = [i["name"] for i in components_to_be_deleted]
    app_names = sorted(set(asset_all_names))

    apps_with_versions_list = {}
    # iterate over app names to get versions
    for i in app_names:
        list_versions = []
        [
            list_versions.append(j["version"])
            for j in components_to_be_deleted
            if i == j["name"]
        ]
        apps_with_versions_list[i] = list_versions

    # 1 condition - to keep {remain_count} artifacts
    for k, v in apps_with_versions_list.items():
        versions_except_remain_count = sorted(
            v,
            key=lambda x: [int(val) if val.isdigit() else val for val in x.split(".")],
            reverse=True,
        )[remain_count:]

        apps_with_versions_list[k] = versions_except_remain_count

    asset_data = []

    for component in components_to_be_deleted:
        for app, versions in apps_with_versions_list.items():
            if component["name"] == app and component["version"] in versions:
                [
                    asset_data.append(
                        {
                            "id": asset["id"],
                            "format": asset["format"],
                            "repository": asset["repository"],
                            "path": asset["path"],
                            "fileSize": asset["fileSize"],
                            "lastModified": asset["lastModified"],
                            "lastDownloaded": asset["lastDownloaded"],
                        }
                    )
                    for asset in component["assets"]
                ]

    # Sort the list by the custom sorting key
    sorted_asset_data = sorted(asset_data, key=sort_key, reverse=True)

    assets_to_be_deleted = []

    # counter for assets to be deleted
    assets_count = 0

    if len(sorted_asset_data) > 0:

        for asset in sorted_asset_data:
            # get asset age in days
            asset_age_days = (
                today_datetime - sort_key(asset).replace(tzinfo=None)
            ).days

            # 2 condition - Delete artifacts older than {remain_days}
            # make a new list of assets to be deleted
            if asset_age_days > remain_days:
                assets_to_be_deleted.append(asset)

        for item in assets_to_be_deleted:
            logging.info(
                f'Deleting: {item["path"]}, lastDownloaded - {item["lastDownloaded"]}, lastModified - {item["lastModified"]}'
            )
            assets_count += 1

            if dry_run == "true":
                logging.info(
                    f'DRY-RUN mode: requests.delete({nexus_url}/v1/assets/{item["id"]}, auth=auth)'
                )
            else:
                x = requests.delete(f'{nexus_url}/v1/assets/{item["id"]}', auth=auth)
                logging.info(f"Response code: {x.status_code}")

    else:
        logging.info("No obsolete assets to delete")

    logging.info(f"Empty components deleted: {empty_components_count}")
    logging.info(f"Assets deleted: {assets_count}")


if __name__ == "__main__":
    start = time.time()
    asyncio.run(main())
    logging.info("Time taken: %.2f seconds", time.time() - start)
