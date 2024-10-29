# nexus3_clean

## Description

This script is used to clean up maven repositories using Nexus API (https://help.sonatype.com/en/rest-and-integration-api.html) for Nexus 3.72+.

We can set for each repository:
* number of days since lastDownloaded (remain_days)
* how many artifacts leave at least (remain_count)

All components which don't fit these conditions to be deleted


## usage

```
pip install -r requirements.txt
python nexus_clean_by_date_and_count_maven.py
```

By default script executes in dry-run mode and doesn't clean up until you set dry-run to `false`
