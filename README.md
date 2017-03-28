**Application**
TriggerDockerBuild

**Description**
A Python script to monitor GitHub, Arch Repository and Arch User Repository for version changes, if a change is identified then we create a new GitHub release which then triggers a Docker Hub build.

**Features**
- GitHub monitoring
- Arch Repository monitoring
- Arch User Repository (AUR) monitoring

**Windows Installation**
TBA

**Linux Installation**
- Install Python 2.7.x
- Download the zipped source from https://github.com/binhex/trigger-docker-build/archive/master.zip
- Unpack the zipped sounrce

**Usage**
```
./lib/pex/TriggerDockerBuild.pex ./TriggerDockerBuild.py --daemon
```

**Changelog**
ver 1.0.0

**Future**
- Use Beautiful Soup to also web scrape for non API sites.
- Monitor Docker Hub "Build Details" for success/failure.
- Add in action of notify or trigger for version changes.
- Send Kodi notification.

**Known Issues**
- TBA
___
If you appreciate my work, then please consider buying me a beer  :D

[![PayPal donation](https://www.paypal.com/en_US/i/btn/btn_donate_SM.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=H8PWP3RLBDCBQ)