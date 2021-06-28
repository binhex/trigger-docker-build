**Application:**
TriggerDockerBuild

**Description:**
A Python script to monitor GitHub, Arch Repository and Arch User Repository for version changes, if a change is identified then we create a new GitHub release which then triggers a Docker Hub build.

**Features:**
- Support for Github release or tag monitoring.
- Arch Official Repository (AOR) monitoring
- Arch User Repository (AUR) monitoring
- Support for trigger or notify actions
- Email notification
- Kodi notification

**Windows Installation:**
Not supported

**Linux Installation:**
- Install Python 2.7.x
- Download the zipped source from https://github.com/binhex/trigger-docker-build/archive/master.zip
- Unpack the zipped source

**Configuration:**
```
site_list = [{'source_site_name': '<github|aor|aur>', 'source_repo_name': '<repo_name>', 'source_app_name': '<app_name>', 'source_query_type': 'release|tag', 'target_repo_name': '<repo_name>', 'action': '<notify|trigger>'}]
```

**Usage:**
```
./lib/pex/TriggerDockerBuild.pex ./TriggerDockerBuild.py --daemon
```

**Future:**
- Use Beautiful Soup to also web scrape for non API sites.

**Known Issues:**
- TBA
___
If you appreciate my work, then please consider buying me a beer  :D

[![PayPal donation](https://www.paypal.com/en_US/i/btn/btn_donate_SM.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=H8PWP3RLBDCBQ)