**Application:**
TriggerDockerBuild

**Description:**
A Python script to monitor GitHub, Arch Repository and Arch User Repository for version changes, if a change is identified then we create a new GitHub release which then triggers the GitHub Action to build, test and push the Docker image to multiple Docker registries.

**Features:**
- GitHub release, tag, branch, and pre-release monitoring.
- GitLab branch monitoring.
- PyPI package version monitoring.
- Arch Official Repository (AOR) monitoring.
- Arch User Repository (AUR) monitoring.
- Regex-based monitoring for Minecraft Bedrock and Java editions.
- Support for trigger (create release) or notify (email) actions.
- Email notification with HTML formatting.
- Kodi notification.

**Windows Installation:**
Not supported

**Linux Installation:**
- Install Python 3.6+
- Install pip
- Clone this repository from https://github.com/binhex/trigger-docker-build
- Install dependencies: `pip install -r requirements.txt`

**Configuration:**
```
site_list = [{'source_site_name': '<github|gitlab|pypi|aor|aur|regex>', 'source_repo_name': '<repo_name>', 'source_app_name': '<app_name>', 'source_query_type': 'release|tag|pre-release|branch', 'source_branch_name': '<branch>', 'target_repo_name': '<repo_name>', 'action': '<notify|trigger>', 'target_release_days': '<days>', 'grace_period_mins': '<mins>'}]
```

**Usage:**
```
python3 ./TriggerDockerBuild.py --config ./configs --logs ./logs

# Or with scheduling enabled:
python3 ./TriggerDockerBuild.py --config ./configs --logs ./logs --schedule
```

**Future:**
- Use Beautiful Soup to also web scrape for non API sites.

**Known Issues:**
- TBA
___
If you appreciate my work, then please consider buying me a beer  :D

[![PayPal donation](https://www.paypal.com/en_US/i/btn/btn_donate_SM.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=H8PWP3RLBDCBQ)
