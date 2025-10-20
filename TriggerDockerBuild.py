import requests
import configobj
import validate
import argparse
import os
import sys
import re
import socket
import logging
import logging.handlers
import backoff
import json
import yagmail
import schedule
import time
import daemon
import urllib3
import signal
import kodijson
import datetime
from bs4 import BeautifulSoup

# hack to workaround bs not being compatible with python 3.10 see
# https://stackoverflow.com/questions/69515086/error-attributeerror-collections-has-no-attribute-callable-using-beautifu
# import collections
# collections.Callable = collections.abc.Callable

urllib3.disable_warnings()  # required to suppress ssl warning for urllib3 (requests uses urllib3)
signal.signal(signal.SIGINT, signal.default_int_handler)  # ensure we correctly handle all keyboard interrupts

# TODO change input to functions as dictionary
# TODO change functions to **kwargs and use .get() to get value (will be none if not fund)
# TODO change return for function to dictionary


def create_config():

    validator = validate.Validator()
    config_obj.validate(validator, copy=True)
    config_obj.filename = config_ini
    config_obj.write()


def time_check(current_time, grace_period_mins, source_version_change_datetime):

    # compare difference between local date/time and trigger date/time to produce timedelta
    time_delta = current_time - source_version_change_datetime
    app_logger_instance.debug(u"Time delta object is %s" % time_delta)

    # turn timedelta object into minutes
    time_delta_secs = datetime.timedelta.total_seconds(time_delta)
    time_delta_mins = int(time_delta_secs) / 60

    grace_period_mins_int = int(grace_period_mins)

    # check if time_delta is greater than or equal to grace_period_mins
    if time_delta_mins >= grace_period_mins_int:

        app_logger_instance.info(u"Time since last update (%s mins) >= to grace period (%s mins)" % (time_delta_mins, grace_period_mins))
        return True

    else:
        app_logger_instance.info(u"Time since last update (%s mins) < grace period (%s mins)" % (time_delta_mins, grace_period_mins))
        return False


def app_logging():

    # read log levels
    log_level = config_obj["general"]["log_level"]

    # setup formatting for log messages
    app_formatter = logging.Formatter("%(asctime)s %(threadName)s %(module)s %(funcName)s :: [%(levelname)s] %(message)s")

    # setup logger for app
    app_logger = logging.getLogger("app")

    # add rotating log handler
    app_rotatingfilehandler = logging.handlers.RotatingFileHandler(app_log_file, "a", maxBytes=10485760, backupCount=3, encoding="utf-8")

    # set formatter for app
    app_rotatingfilehandler.setFormatter(app_formatter)

    # add the log message handler to the logger
    app_logger.addHandler(app_rotatingfilehandler)

    # set level of logging from config
    if log_level == "INFO":

        app_logger.setLevel(logging.INFO)

    elif log_level == "WARNING":

        app_logger.setLevel(logging.WARNING)

    elif log_level == "exception":

        app_logger.setLevel(logging.ERROR)

    elif log_level == "debug":

        app_logger.setLevel(logging.DEBUG)

    # setup logging to console
    console_streamhandler = logging.StreamHandler()

    # set formatter for console
    console_streamhandler.setFormatter(app_formatter)

    # add handler for formatter to the console
    app_logger.addHandler(console_streamhandler)

    # set level of logging from config
    if log_level == "INFO":

        console_streamhandler.setLevel(logging.INFO)

    elif log_level == "WARNING":

        console_streamhandler.setLevel(logging.WARNING)

    elif log_level == "exception":

        console_streamhandler.setLevel(logging.ERROR)

    elif log_level == "debug":

        console_streamhandler.setLevel(logging.DEBUG)

    return {'logger': app_logger, 'handler': app_rotatingfilehandler}


def notification_email(**kwargs):

    if not email_notification:

        app_logger_instance.info(u"Email notification not enabled")
        return 1

    # unpack arguments from dictionary
    action = kwargs.get("action")
    msg_type = kwargs.get("msg_type")
    error_msg = kwargs.get("error_msg")
    source_app_name = kwargs.get("source_app_name")
    source_repo_name = kwargs.get("source_repo_name")
    source_site_name = kwargs.get("source_site_name")
    source_site_url = kwargs.get("source_site_url")
    target_repo_name = kwargs.get("target_repo_name")
    previous_version = kwargs.get("previous_version")
    current_version = kwargs.get("current_version")

    if msg_type == "site_error":

        yag = yagmail.SMTP(email_username, email_password)
        subject = '%s - %s' % (source_site_name, msg_type)
        html = '''
        <b>Source Site Name:</b> %s<br>
        <b>Source Site URL:</b>  <a href="%s">%s</a><br>
        <b>Error Message:</b> %s
        ''' % (source_site_name, source_site_url, source_site_name, error_msg)

    elif msg_type == "config_error" or msg_type == "app_error":

        yag = yagmail.SMTP(email_username, email_password)
        subject = '%s - %s' % (source_app_name, msg_type)
        html = '''
        <b>Source Site Name:</b> %s<br>
        <b>Source Repository:</b> %s<br>
        <b>Source Site URL:</b>  <a href="%s">%s</a><br>
        <b>Error Message:</b> %s
        ''' % (source_site_name, source_repo_name, source_site_url, source_app_name, error_msg)

    else:

        target_repo_owner = config_obj["general"]["target_repo_owner"]

        # construct url to docker hub build details
        dockerhub_build_details = "https://hub.docker.com/r/%s/%s/tags?page=1&ordering=last_updated&name=latest" % (target_repo_owner, target_repo_name)

        # construct url to github workflow details
        github_action_details = "https://github.com/%s/%s/actions" % (target_repo_owner, target_repo_name)

        # construct url to github container registry details
        github_ghcr_details = "https://github.com/users/%s/packages/container/package/%s" % (target_repo_owner, target_repo_name)

        yag = yagmail.SMTP(email_username, email_password)
        subject = '%s [%s] - updated to %s' % (source_app_name, action, current_version)
        html = '''
        <b>Action:</b> %s<br>
        <b>Previous Version:</b> %s<br>
        <b>Current Version:</b> %s<br>
        <b>Source Site Name:</b> %s<br>
        <b>Source Repository:</b> %s<br>
        <b>Source Site URL:</b>  <a href="%s">%s</a>
        ''' % (action, previous_version, current_version, source_site_name, source_repo_name, source_site_url, source_app_name)

        if action == "trigger":

            html += '''
            <b>Target Repository URL:</b> <a href="https://github.com/%s/%s">github repo</a><br>
            <b>Target Github Action URL:</b> <a href="%s">github workflow</a><br>
            <b>Target Github Container Registry URL:</b> <a href="%s">github registry</a><br>
            <b>Target Docker Hub Registry URL:</b> <a href="%s">dockerhub registry</a>
            ''' % (target_repo_owner, target_repo_name, github_action_details, github_ghcr_details, dockerhub_build_details)

    try:

        app_logger_instance.info(u'Sending email notification...')
        yag.send(to=email_to, subject=subject, contents=[html])

    except Exception:

        app_logger_instance.warning(u"Failed to send E-Mail notification to %s" % email_to)
        return 1


# noinspection PyUnresolvedReferences
def notification_kodi(action, source_app_name, current_version):

    if not kodi_notification:

        app_logger_instance.info(u"Kodi notification not enabled")
        return 1

    # read kodi config
    kodi_username = config_obj["notification"]["kodi_username"]
    kodi_hostname = config_obj["notification"]["kodi_hostname"]
    kodi_port = config_obj["notification"]["kodi_port"]

    # construct login with custom credentials for rpc call
    kodi = kodijson.Kodi("http://%s:%s/jsonrpc" % (kodi_hostname, kodi_port), kodi_username, kodi_password)

    # send gui notification
    try:

        app_logger_instance.info(u'Sending kodi notification...')
        kodi.GUI.ShowNotification({"title": "TriggerDockerBuild", "message": "%s [%s] - updated to %s" % (source_app_name, action, current_version)})

    except Exception:

        app_logger_instance.warning(u"Failed to send notification to Kodi instance at http://%s:%s/jsonrpc" % (kodi_hostname, kodi_port))
        return 1


@backoff.on_exception(backoff.expo, (socket.timeout, requests.exceptions.Timeout, requests.exceptions.HTTPError), max_tries=10)
def http_client(**kwargs):

    if kwargs is not None:

        if "url" in kwargs:

            url = kwargs['url']

        else:

            app_logger_instance.warning(u'No URL sent to function, exiting function...')
            return 1, None, None

        if "user_agent" in kwargs:

            user_agent = kwargs['user_agent']

        else:

            app_logger_instance.warning(u'No User Agent sent to function, exiting function...')
            return 1, None, None

        if "request_type" in kwargs:

            request_type = kwargs['request_type']

        else:

            app_logger_instance.warning(u'No request type (get/put/post) sent to function, exiting function...')
            return 1, None, None

        # optional stuff to include
        if "auth" in kwargs:

            auth = kwargs['auth']

        else:

            auth = None

        if "additional_header" in kwargs:

            additional_header = kwargs['additional_header']

        else:

            additional_header = None

        if "data_payload" in kwargs:

            data_payload = kwargs['data_payload']

        else:

            data_payload = None

    else:

        app_logger_instance.warning(u'No keyword args sent to function, exiting function...')
        return 1, None, None

    # set connection timeout value (max time to wait for connection)
    connect_timeout = 60.0

    # set read timeout value (max time to wait between each byte)
    read_timeout = 60.0

    # use a session instance to customize how "requests" handles making http requests
    session = requests.Session()

    # set status_code and content to None in case nothing returned
    status_code = None

    try:

        # define dict of common arguments for requests
        requests_data_dict = {'url': url, 'timeout': (connect_timeout, read_timeout), 'allow_redirects': True, 'verify': False}

        # define default headers to compress and fake user agent
        session.headers.update({
            'Accept-encoding': 'gzip',
            'User-Agent': user_agent
        })

        if "additional_header" in kwargs:

            # append to headers dict with additional headers dict
            session.headers.update(additional_header)

        if "auth" in kwargs:

            session.auth = auth

        if request_type == "put":

            # add additional keyword arguments
            requests_data_dict.update({'data': data_payload})

        elif request_type == "post":

            # add additional keyword arguments
            requests_data_dict.update({'data': data_payload})

        # construct class.method from request_type
        request_method = getattr(session, request_type)

        # use keyword argument unpack to convert dict to keyword args
        response = request_method(**requests_data_dict)

        # get status code and content returned
        status_code = response.status_code
        content = response.content

        if status_code == 401:

            app_logger_instance.warning(u"The status code %s indicates unauthorised access for %s, error is %s" % (status_code, url, content))
            raise requests.exceptions.HTTPError(status_code, url, content)

        elif status_code == 404:

            app_logger_instance.warning(u"The status code %s indicates the requested resource could not be found  for %s, error is %s" % (status_code, url, content))
            raise requests.exceptions.HTTPError(status_code, url, content)

        elif status_code == 422:

            app_logger_instance.warning(u"The status code %s indicates a request was well-formed but was unable to be followed due to semantic errors for %s, error is %s" % (status_code, url, content))
            raise requests.exceptions.HTTPError(status_code, url, content)

        elif not 200 <= status_code <= 299:

            app_logger_instance.warning(u"The status code %s indicates an unexpected error for %s, error is %s" % (status_code, url, content))
            raise requests.exceptions.HTTPError(status_code, url, content)

    except requests.exceptions.ConnectTimeout as content:

        # connect timeout occurred
        app_logger_instance.warning(u"Connection timeout for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.ConnectionError as content:

        # connection error occurred
        app_logger_instance.warning(u"Connection error for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.TooManyRedirects as content:

        # too many redirects, bad site or circular redirect
        app_logger_instance.warning(u"Too many retries for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.HTTPError as content:

        # catch http exceptions thrown by requests
        return 1, status_code, content

    except requests.exceptions.ReadTimeout as content:
        # too many redirects, bad site or circular redirect
        app_logger_instance.warning(u"Read timeout for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.RequestException as content:

        # catch any other exceptions thrown by requests
        app_logger_instance.warning(u"Caught other exceptions for URL %s with error %s" % (url, content))
        return 1, status_code, content

    else:

        if 200 <= status_code <= 299:

            app_logger_instance.info(u"The status code %s indicates a successful request for %s" % (status_code, url))
            return 0, status_code, content


def github_create_release(current_version, target_repo_branch, target_repo_owner, target_repo_name, user_agent):

    # remove illegal characters from version (github does not allow certain chars for release name)
    current_version = re.sub(r":", r".", current_version, flags=re.IGNORECASE)

    app_logger_instance.info(u"Creating Release on GitHub for version %s..." % current_version)

    github_tag_name = "%s-01" % current_version
    github_release_name = "API/URL triggered release"
    github_release_body = github_tag_name
    request_type = "post"
    http_url = 'https://api.github.com/repos/%s/%s/releases' % (target_repo_owner, target_repo_name)
    data_payload = '{"tag_name": "%s", "target_commitish": "%s", "name": "%s", "body": "%s", "draft": false, "prerelease": false}' % (github_tag_name, target_repo_branch, github_release_name, github_release_body)

    # process post request
    return_code, status_code, content = http_client(url=http_url, user_agent=user_agent, additional_header={'Authorization': 'token %s' % target_access_token}, request_type=request_type, data_payload=data_payload)
    return return_code, status_code, content


def check_site(**kwargs):

    # unpack arguments from dictionary
    url = kwargs.get("url")
    user_agent = kwargs.get("user_agent")
    site_name = kwargs.get("site_name")

    # construct url to github rest api
    request_type = "get"

    # set number of retries and set default site_down boolean
    retries = 10
    sleep_secs = 60
    site_down = True

    while True:

        # download json content
        return_code, status_code, content = http_client(url=url, user_agent=user_agent, additional_header={'Authorization': 'token %s' % target_access_token}, request_type=request_type)

        if return_code == 0:
            site_down = False
            app_logger_instance.debug(f"'{site_name}' site operational for '{url}'")
            break
        else:
            app_logger_instance.info(f"Having issues connecting to '{site_name}' for '{url}', retrying in '{sleep_secs}' seconds...")
            time.sleep(sleep_secs)
            retries = retries - 1

        if retries <= 0:
            app_logger_instance.warning(f"'{site_name}' site down for '{url}'")
            break

    if site_down:

        msg_type = "site_error"
        error_msg = f"{site_name} site down - '{url}'"
        notification_email(msg_type=msg_type, error_msg=error_msg, source_site_name=site_name, source_site_url=url)
        app_logger_instance.warning(error_msg)

    # convert the following then compare against throttle days value "2020-04-15T21:53:20Z"
    return site_down


def github_target_last_release_date(target_repo_owner, target_repo_name, user_agent):

    github_query_type = "releases/latest"
    json_query = "published_at"

    # construct url to github rest api
    url = "https://api.github.com/repos/%s/%s/%s" % (target_repo_owner, target_repo_name, github_query_type)
    request_type = "get"

    # download json content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent, additional_header={'Authorization': 'token %s' % target_access_token}, request_type=request_type)

    if return_code == 0:

        try:

            content = json.loads(content)

        except (ValueError, TypeError, KeyError):

            app_logger_instance.info(u"Problem loading json from %s" % url)
            return 1, None

    else:

        app_logger_instance.info(u"Problem downloading json content from %s" % url)
        return 1, None

    try:

        # get release date from json
        target_last_release_date = content['%s' % json_query]

    except IndexError:

        app_logger_instance.info(u"Problem parsing json from %s, skipping to next iteration..." % url)
        return 1, None

    # convert the following then compare against throttle days value "2020-04-15T21:53:20Z"
    return 0, target_last_release_date


def github_apps(source_app_name, source_query_type, source_repo_name, user_agent, source_branch_name):

    # certain github repos do not have releases, only tags, thus we need to account for these differently
    if source_query_type.lower() == "tag":

        github_query_type = "tags"
        json_query = "name"

    elif source_query_type.lower() == "pre-release":

        github_query_type = "releases"
        json_query = "tag_name"

    elif source_query_type.lower() == "release":

        github_query_type = "releases/latest"
        json_query = "tag_name"

    elif source_query_type.lower() == "branch":

        github_query_type = "commits"
        json_query = "sha"

    else:

        app_logger_instance.warning(u"source_query_type '%s' is not valid, skipping to next iteration..." % source_query_type.lower())
        return None, None

    # construct url for package details
    source_site_url = "https://github.com/%s/%s/%s" % (source_repo_name, source_app_name, github_query_type)

    # construct url to github rest api
    url = "https://api.github.com/repos/%s/%s/%s" % (source_repo_name, source_app_name, github_query_type)

    # if github branch then we specify the branch name via 'sha' parameter
    if source_query_type.lower() == "branch":

        url = "%s?sha=%s" % (url, source_branch_name)

    request_type = "get"

    # download json content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent, additional_header={'Authorization': 'token %s' % target_access_token}, request_type=request_type)

    if return_code == 0:

        try:

            content = json.loads(content)

        except (ValueError, TypeError, KeyError):

            app_logger_instance.info(u"Problem loading json from %s" % url)
            return None, source_site_url

    else:

        app_logger_instance.info(u"Problem downloading json content from %s" % url)
        return None, source_site_url

    try:

        if github_query_type == "tags" or github_query_type == "commits":

            # get tag/sha from json
            current_version = content[0]['%s' % json_query]

        elif github_query_type == "releases/latest":

            # get release from json
            current_version = content['%s' % json_query]

        else:

            app_logger_instance.warning(u"Unknown Github query type of '%s', skipping to next iteration..." % github_query_type)
            return None, source_site_url

    except IndexError:

        app_logger_instance.warning(u"Problem parsing json from %s, skipping to next iteration..." % url)
        return None, source_site_url

    if source_query_type.lower() == "branch":

        source_site_url = "%s/%s" % (source_site_url, source_branch_name)

    return current_version, source_site_url


def gitlab_apps(source_app_name, source_repo_name, source_project_id, source_branch_name, source_query_type, user_agent):

    # use gitlab rest api
    url = 'https://gitlab.com/api/v4/projects/%s/repository/commits/%s' % (source_project_id, source_branch_name)

    # construct url for package details
    source_site_url = 'https://gitlab.com/%s/%s' % (source_repo_name, source_app_name)

    request_type = "get"

    if source_query_type.lower() == "branch":

        json_query = "id"

    else:

        app_logger_instance.warning(u"source_query_type '%s' is not valid, skipping to next iteration..." % source_query_type.lower())
        return None, source_site_url

    # download webpage content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent, request_type=request_type)

    if return_code == 0:

        try:

            # decode json
            content = json.loads(content)

        except (ValueError, TypeError, KeyError, IndexError):

            app_logger_instance.info(u"Problem loading json from %s" % url)
            return None, source_site_url

    else:

        app_logger_instance.info(u"Problem downloading json content from %s" % url)
        return None, source_site_url

    try:

        # construct app version
        current_version = content['%s' % json_query]

    except (ValueError, TypeError, KeyError, IndexError):

        app_logger_instance.info(u"Problem parsing json from %s, skipping to next iteration..." % url)
        return None, source_site_url

    return current_version, source_site_url


def pypi_apps(source_app_name, user_agent):

    # use pypi json to get python package version
    url = "https://pypi.org/pypi/%s/json" % source_app_name
    request_type = "get"

    # construct url for package details
    source_site_url = f"https://pypi.org/search/?q={source_app_name}"

    # download webpage content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent, request_type=request_type)

    if return_code == 0:

        try:

            # decode json
            content = json.loads(content)

        except (ValueError, TypeError, KeyError, IndexError):

            app_logger_instance.info(u"Problem loading json from %s" % url)
            return None, source_site_url

    else:

        app_logger_instance.info(u"Problem downloading json content from %s" % url)
        return None, source_site_url

    current_version = content['info']['version']

    return current_version, source_site_url


def aor_apps(source_app_name, user_agent):

    # use aor unofficial api to get app release info
    url = 'https://archlinux.org/packages/search/json/?q=%s' % source_app_name
    request_type = "get"

    # construct url for package details
    source_site_url = f"https://archlinux.org/packages/?sort=&q={source_app_name}&maintainer=&flagged="

    # download webpage content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent, request_type=request_type)

    try:

        # decode json
        content = json.loads(content)

        # filter python objects with list comprehension to prevent fuzzy mismatch
        content = [x for x in content['results'] if x['pkgname'] == source_app_name]

    except (ValueError, TypeError, KeyError, IndexError):

        app_logger_instance.info(u"Problem loading json from %s" % url)
        return None, source_site_url

    try:

        # get package version and release number from json
        pkgver = content[0]['pkgver']
        pkgrel = content[0]['pkgrel']

        # construct app version
        current_version = "%s-%s" % (pkgver, pkgrel)

    except (ValueError, TypeError, KeyError, IndexError):

        app_logger_instance.info(u"Problem parsing json from %s, skipping to next iteration..." % url)
        return None, source_site_url

    return current_version, source_site_url


def aur_apps(source_app_name, user_agent):

    # use aur api to get app release info
    url = "https://aur.archlinux.org/rpc/?v=5&type=info&arg[]=%s" % source_app_name
    request_type = "get"

    # construct url for package details
    source_site_url = "https://aur.archlinux.org/packages/%s/" % source_app_name

    # download webpage content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent, request_type=request_type)

    if return_code == 0:

        try:

            content = json.loads(content)

        except (ValueError, TypeError, KeyError):

            app_logger_instance.info(u"Problem loading json from %s" % url)
            return None, source_site_url

    else:

        app_logger_instance.info(u"Problem downloading json content from %s" % url)
        return None, source_site_url

    try:

        # get app version from json
        current_version = content["results"][0]["Version"]

    except IndexError:

        app_logger_instance.info(u"Problem parsing json from %s, skipping to next iteration..." % url)
        return None, source_site_url

    return current_version, source_site_url


def soup_regex(source_site_url, user_agent):

    # download webpage
    request_type = "get"

    # download webpage content
    return_code, status_code, content = http_client(url=source_site_url, user_agent=user_agent, request_type=request_type)

    if return_code == 0:

        try:

            soup = BeautifulSoup(content, features="html.parser")

        except (ValueError, TypeError, KeyError):

            app_logger_instance.info(u"Problem extracting url using regex from url  %s" % source_site_url)
            return None, None

    else:

        app_logger_instance.info(u"Problem downloading webpage from url  %s" % source_site_url)
        return None, None

    return soup


def monitor_sites():

    # read sites list from config
    config_site_list = config_obj["monitor_sites"]["site_list"]
    target_repo_owner = config_obj["general"]["target_repo_owner"]

    # pretend to be windows 10 running chrome (required for minecraft bedrock)
    user_agent_chrome = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36'

    # check github api is operational
    url = "https://api.github.com"

    site_down_github = check_site(url=url, user_agent=user_agent_chrome, site_name='GitHub')

    # check gitlab rest api is operational
    url = "https://gitlab.com/api/v4/projects"

    site_down_gitlab = check_site(url=url, user_agent=user_agent_chrome, site_name='GitLab')

    # check pypi website is operational
    test_package = 'requests'
    url = f"https://pypi.org/pypi/{test_package}/json"

    site_down_pypi = check_site(url=url, user_agent=user_agent_chrome, site_name='PyPi')

    # check aor site is operational
    test_package = 'base'
    url = f'https://archlinux.org/packages/core/any/{test_package}/'

    site_down_aor = check_site(url=url, user_agent=user_agent_chrome, site_name='AOR')

    # check aur site is operational
    test_package = 'yay'
    url = f"https://aur.archlinux.org/rpc/?v=5&type=info&arg[]={test_package}"

    site_down_aur = check_site(url=url, user_agent=user_agent_chrome, site_name='AUR')

    # set counter for number of failures to get app package details
    app_down_gitlab_counter = 0
    app_down_github_counter = 0
    app_down_pypi_counter = 0
    app_down_aor_counter = 0
    app_down_aur_counter = 0

    # set maximum number of email notifications for failed downloads before skipping
    app_down_counter_max = 3

    # loop over each site and check previous and current result
    for site_item in config_site_list:

        source_site_name = site_item.get("source_site_name")
        source_app_name = site_item.get("source_app_name")
        source_repo_name = site_item.get("source_repo_name")
        source_project_id = site_item.get("source_project_id")
        source_branch_name = site_item.get("source_branch_name")
        target_release_days = site_item.get("target_release_days")
        target_repo_name = site_item.get("target_repo_name")
        target_repo_branch = site_item.get("target_repo_branch")
        source_query_type = site_item.get("source_query_type")
        grace_period_mins = site_item.get("grace_period_mins")
        source_version_change_datetime = site_item.get("source_version_change_datetime")
        action = site_item.get("action")

        # set default values in case they are not supplied
        source_site_url = None

        app_logger_instance.info(u"-------------------------------------")
        app_logger_instance.info(u"Processing started for application %s..." % source_app_name)

        if action != "notify":

            # if target branch not defined then send email notification and skip to next item
            if target_repo_branch is None:

                msg_type = "config_error"
                error_msg = u"Target repo branch not defined for target repo '%s', skipping to next iteration..." % target_repo_name
                notification_email(msg_type=msg_type, error_msg=error_msg, source_site_name=source_site_name, source_repo_name=source_repo_name, source_app_name=source_app_name, source_site_url=source_site_url)
                app_logger_instance.warning(error_msg)
                continue

        if source_site_name == "github":

            if site_down_github:

                app_logger_instance.warning(u"Site '%s' marked as down, skipping processing for application '%s'..." % (source_site_name, source_app_name))
                continue

            current_version, source_site_url = github_apps(source_app_name, source_query_type, source_repo_name, user_agent_chrome, source_branch_name)

            if current_version is None:

                error_msg = f"Unable to connect to site '{source_site_name}' for application '{source_app_name}', skipping to next iteration..."

                # increment counter for number of failed app detail downloads
                app_down_github_counter += 1

                # if number of failed app package detail downloads above limit then silence email notifications
                if app_down_github_counter <= app_down_counter_max:

                    msg_type = "app_error"
                    notification_email(msg_type=msg_type, error_msg=error_msg, source_site_name=source_site_name, source_repo_name=source_repo_name, source_app_name=source_app_name, source_site_url=source_site_url)

                else:

                    app_logger_instance.info(f"Number of failed downloads for site '{source_site_name}' has exceeded '{app_down_counter_max}', skipping notifications")

                app_logger_instance.warning(error_msg)
                continue

        elif source_site_name == "gitlab":

            if site_down_gitlab:

                app_logger_instance.warning(u"Site '%s' marked as down, skipping processing for application '%s'..." % (source_site_name, source_app_name))
                continue

            current_version, source_site_url = gitlab_apps(source_app_name, source_repo_name, source_project_id, source_branch_name, source_query_type, user_agent_chrome)

            if current_version is None:

                error_msg = f"Unable to connect to site '{source_site_name}' for application '{source_app_name}', skipping to next iteration..."

                # increment counter for number of failed app detail downloads
                app_down_gitlab_counter += 1

                # if number of failed app package detail downloads above limit then silence email notifications
                if app_down_gitlab_counter <= app_down_counter_max:

                    msg_type = "app_error"
                    notification_email(msg_type=msg_type, error_msg=error_msg, source_site_name=source_site_name, source_repo_name=source_repo_name, source_app_name=source_app_name, source_site_url=source_site_url)

                else:

                    app_logger_instance.info(f"Number of failed downloads for site '{source_site_name}' has exceeded '{app_down_counter_max}', skipping notifications")

                app_logger_instance.warning(error_msg)
                continue

        elif source_site_name == "pypi":

            if site_down_pypi:

                app_logger_instance.warning(u"Site '%s' marked as down, skipping processing for application '%s'..." % (source_site_name, source_app_name))
                continue

            current_version, source_site_url = pypi_apps(source_app_name, user_agent_chrome)

            if current_version is None:

                error_msg = f"Unable to connect to site '{source_site_name}' for application '{source_app_name}', skipping to next iteration..."

                # increment counter for number of failed app detail downloads
                app_down_pypi_counter += 1

                # if number of failed app package detail downloads above limit then silence email notifications
                if app_down_pypi_counter <= app_down_counter_max:

                    msg_type = "app_error"
                    notification_email(msg_type=msg_type, error_msg=error_msg, source_site_name=source_site_name, source_repo_name=source_repo_name, source_app_name=source_app_name, source_site_url=source_site_url)

                else:

                    app_logger_instance.info(f"Number of failed downloads for site '{source_site_name}' has exceeded '{app_down_counter_max}', skipping notifications")

                app_logger_instance.warning(error_msg)
                continue

        elif source_site_name == "aor":

            if site_down_aor:

                app_logger_instance.warning(u"Site '%s' marked as down, skipping processing for application '%s'..." % (source_site_name, source_app_name))
                continue

            # if grace period not defined then set to default value (required for aor)
            if grace_period_mins is None:

                grace_period_mins = 60

            current_version, source_site_url = aor_apps(source_app_name, user_agent_chrome)

            if current_version is None:

                error_msg = f"Unable to connect to site '{source_site_name}' for application '{source_app_name}', skipping to next iteration..."

                # increment counter for number of failed app detail downloads
                app_down_aor_counter += 1

                # if number of failed app package detail downloads above limit then silence email notifications
                if app_down_aor_counter <= app_down_counter_max:

                    msg_type = "app_error"
                    notification_email(msg_type=msg_type, error_msg=error_msg, source_site_name=source_site_name, source_repo_name=source_repo_name, source_app_name=source_app_name, source_site_url=source_site_url)

                else:

                    app_logger_instance.info(f"Number of failed downloads for site '{source_site_name}' has exceeded '{app_down_counter_max}', skipping notifications")

                app_logger_instance.warning(error_msg)
                continue

        elif source_site_name == "aur":

            if site_down_aur:

                app_logger_instance.warning(u"Site '%s' marked as down, skipping processing for application '%s'..." % (source_site_name, source_app_name))
                continue

            current_version, source_site_url = aur_apps(source_app_name, user_agent_chrome)

            if current_version is None:

                error_msg = f"Unable to connect to site '{source_site_name}' for application '{source_app_name}', skipping to next iteration..."

                # increment counter for number of failed app detail downloads
                app_down_aur_counter += 1

                # if number of failed app package detail downloads above limit then silence email notifications
                if app_down_aur_counter <= app_down_counter_max:

                    msg_type = "app_error"
                    notification_email(msg_type=msg_type, error_msg=error_msg, source_site_name=source_site_name, source_repo_name=source_repo_name, source_app_name=source_app_name, source_site_url=source_site_url)

                else:

                    app_logger_instance.info(f"Number of failed downloads for site '{source_site_name}' has exceeded '{app_down_counter_max}', skipping notifications")

                app_logger_instance.warning(error_msg)
                continue

        elif source_site_name == "regex":

            if source_app_name == "minecraftbedrock":

                request_type = "get"
                bedrock_unofficial_api = 'https://net-secondary.web.minecraft-services.net/api/v1.0/download/links'
                return_code, status_code, content = http_client(url=bedrock_unofficial_api, user_agent=user_agent_chrome, request_type=request_type)

                if return_code != 0:

                    msg_type = "app_error"
                    error_msg = u"Unable to get bedrock download links from API for app %s, skipping to next iteration..." % source_app_name
                    notification_email(msg_type=msg_type, error_msg=error_msg, source_site_name=source_site_name, source_repo_name=source_repo_name, source_app_name=source_app_name, source_site_url=source_site_url)
                    app_logger_instance.warning(error_msg)
                    continue

                else:

                    try:
                        # Parse the JSON response
                        api_data = json.loads(content)

                        # Find the serverBedrockLinux entry
                        download_url = None
                        for link_item in api_data['result']['links']:
                            if link_item['downloadType'] == 'serverBedrockLinux':
                                download_url = link_item['downloadUrl']
                                break

                        if not download_url:
                            raise KeyError("serverBedrockLinux not found in API response")

                        # Extract version from the download URL using regex
                        # URL format: https://www.minecraft.net/bedrockdedicatedserver/bin-linux/bedrock-server-1.21.90.4.zip
                        version_match = re.search(r'bedrock-server-(.*)\.zip', download_url)
                        if version_match:
                            current_version = version_match.group(1)
                        else:
                            raise ValueError("Could not extract version from download URL")

                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        msg_type = "app_error"
                        error_msg = u"Unable to parse bedrock API response for app %s: %s" % (source_app_name, str(e))
                        notification_email(msg_type=msg_type, error_msg=error_msg, source_site_name=source_site_name, source_repo_name=source_repo_name, source_app_name=source_app_name, source_site_url=source_site_url)
                        app_logger_instance.warning(error_msg)
                        continue

            elif source_app_name == "minecraftserver":

                request_type = "get"
                source_site_url = "https://launchermeta.mojang.com/mc/game/version_manifest.json"

                # get version manifest content
                return_code, status_code, content = http_client(url=source_site_url, user_agent=user_agent_chrome, request_type=request_type)

                if return_code != 0:

                    msg_type = "app_error"
                    error_msg = u"Problem downloading version manifest for url '%s', skipping to next iteration..." % source_site_url
                    notification_email(msg_type=msg_type, error_msg=error_msg, source_site_name=source_site_name, source_repo_name=source_repo_name, source_app_name=source_app_name, source_site_url=source_site_url)
                    app_logger_instance.warning(error_msg)
                    continue

                version_manifest_content = json.loads(content)

                try:

                    current_version = version_manifest_content['latest']['release']

                except (IndexError, KeyError):

                    msg_type = "app_error"
                    error_msg = u"Unable to identify current release version for app '%s', ignoring..." % source_app_name
                    notification_email(msg_type=msg_type, error_msg=error_msg, source_site_name=source_site_name, source_repo_name=source_repo_name, source_app_name=source_app_name, source_site_url=source_site_url)
                    app_logger_instance.warning(error_msg)
                    continue

            else:

                app_logger_instance.warning(u"Source site app %s unknown, skipping to next iteration..." % source_app_name)
                continue

        else:

            app_logger_instance.warning(u"Source site name %s unknown, skipping to next iteration..." % source_site_name)
            continue

        # write value for current match to config
        config_obj["results"]["%s_%s_%s_current_version" % (source_site_name, source_app_name, target_repo_name)] = current_version
        config_obj.write()

        try:

            # read value from previous match from config
            previous_version = config_obj["results"]["%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)]

        except KeyError:

            app_logger_instance.info(u"No known previous version for app %s, assuming first run" % source_app_name)
            app_logger_instance.info(u"Setting previous version to current version %s and going to next iteration" % current_version)
            config_obj["results"]["%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)] = current_version
            config_obj.write()
            continue

        if previous_version != current_version:

            if action == "trigger":

                current_datetime_object = datetime.datetime.now()
                current_datetime_str = current_datetime_object.strftime('%Y-%m-%d %H:%M:%S')

                if grace_period_mins:

                    if source_version_change_datetime is None:

                        app_logger_instance.debug(u"Trigger datetime not defined in config.ini, creating from current datetime")
                        source_version_change_datetime = current_datetime_str

                        site_item["source_version_change_datetime"] = source_version_change_datetime
                        config_obj.write()
                        continue

                    # run function to check if time since last source change is greater than or equal to grace period
                    else:

                        source_version_change_datetime_object = datetime.datetime.strptime(source_version_change_datetime, '%Y-%m-%d %H:%M:%S')

                        if not time_check(current_datetime_object, grace_period_mins, source_version_change_datetime_object):

                            app_logger_instance.info(u"Source version change for app '%s' is less than '%s' mins ago, skipping to next iteration..." % (source_app_name, grace_period_mins))
                            continue

                        else:

                            app_logger_instance.info(u"Source version change for app '%s' is >= '%s' mins ago, proceeding..." % (source_app_name, grace_period_mins))

                if target_release_days:

                    return_code, last_release_date = github_target_last_release_date(target_repo_owner, target_repo_name, user_agent_chrome)

                    if return_code != 0:

                        app_logger_instance.warning(u"Unable to identify target release date for repo '%s', skipping to next iteration..." % target_repo_name)
                        continue

                    target_release_date_object = datetime.datetime.strptime(last_release_date, '%Y-%m-%dT%H:%M:%SZ')

                    # compare difference between local date/time and trigger date/time to produce timedelta
                    target_time_delta = current_datetime_object - target_release_date_object

                    # extract days from time delta
                    target_time_delta_days = target_time_delta.days
                    app_logger_instance.debug(u"Minimum days between target releases is '%s' days" % target_release_days)
                    app_logger_instance.debug(u"Last target release was '%s' days ago" % target_time_delta_days)

                    if int(target_time_delta_days) >= int(target_release_days):

                        app_logger_instance.info(u"Last target release date for app '%s' is >= '%s' days ago, proceeding..." % (target_repo_name, target_release_days))

                    else:

                        app_logger_instance.info(u"Last target release date for app '%s' is less than '%s' days ago, skipping to next iteration..." % (target_repo_name, target_release_days))
                        continue

                app_logger_instance.info(u"Previous version %s and current version %s are different, triggering a docker hub build (via github tag)..." % (previous_version, current_version))
                return_code, status_code, content = github_create_release(current_version, target_repo_branch, target_repo_owner, target_repo_name, user_agent_chrome)

                if status_code == 201:

                    app_logger_instance.info(u"Setting previous version %s to the same as current version %s after successful build" % (previous_version, current_version))

                else:

                    # TODO this is a hack to work around the fact we have converted dict to keyword args
                    regex_code = r'(?<="code":\s")[^"]+'

                    try:
                        code = (re.search(regex_code, str(content))).group(0)
                        if code.lower() == "already_exists":

                            app_logger_instance.warning(u"Problem creating GitHub release as it already exists for '%s/%s', overwriting current version and skipping to next iteration..." % (target_repo_owner, target_repo_name))
                            app_logger_instance.debug(u"Writing current version %s to config.ini" % current_version)
                            config_obj["results"]["%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)] = current_version
                            config_obj.write()

                    except AttributeError:
                        app_logger_instance.warning(u"Problem creating GitHub release due to unknown error for '%s/%s', skipping to next iteration..." % (target_repo_owner, target_repo_name))

                    continue

                if source_version_change_datetime is not None:

                    app_logger_instance.debug(u"Deleting 'source_version_change_datetime', used next time version change occurs")
                    del site_item["source_version_change_datetime"]
                    config_obj.write()

                app_logger_instance.debug(u"Creating 'target_trigger_datetime', used to track when trigger of docker build happened")
                site_item["target_trigger_datetime"] = current_datetime_str
                config_obj.write()

            elif action == "notify":

                app_logger_instance.info(u"Previous version %s and current version %s are different" % (previous_version, current_version))

            app_logger_instance.debug(u"Writing current version %s to config.ini" % current_version)
            config_obj["results"]["%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)] = current_version
            config_obj.write()

            notification_email(action=action, source_app_name=source_app_name, source_repo_name=source_repo_name, source_site_name=source_site_name, source_site_url=source_site_url, target_repo_name=target_repo_name, previous_version=previous_version, current_version=current_version)

            notification_kodi(action, source_app_name, current_version)

        else:

            app_logger_instance.info(u"Previous version %s and current version %s match, nothing to do" % (previous_version, current_version))

        app_logger_instance.info(u"Processing finished for application %s" % source_app_name)

    # write timestamp to config.ini
    config_obj["general"]["last_check"] = time.strftime("%c")
    config_obj.write()


def ondemand_start():

    app_logger_instance.info(u"Checking for version changes...")
    monitor_sites()


def scheduler_start():

    schedule_check_mins = config_obj["general"]["schedule_check_mins"]

    # now run monitor_sites function via scheduler
    schedule.every(schedule_check_mins).minutes.do(monitor_sites)

    while True:

        try:

            schedule.run_pending()
            app_logger_instance.info(u"All applications processed, waiting for next invocation in %s minutes..." % schedule_check_mins)
            time.sleep(1)

        except KeyboardInterrupt:

            app_logger_instance.info(u"Keyboard interrupt received, exiting script...")
            sys.exit()


# required to prevent separate process from trying to load parent process
if __name__ == '__main__':

    version = "1.2.0"

    # custom argparse to redirect user to help if unknown argument specified
    class ArgparseCustom(argparse.ArgumentParser):

        def error(self, message):
            sys.stderr.write('error: %s\n' % message)
            self.print_help()
            sys.exit(2)

    # setup argparse description and usage, also increase spacing for help to 50
    commandline_parser = ArgparseCustom(prog="TriggerDockerBuild", description="%(prog)s " + version, usage="%(prog)s [--help] [--config <path>] [--logs <path>] [--kodi-password <password>] [--email-to <email address>] [--email-username <username>] [--email-password <password>] [--target-access-token <token>] [--pidfile <path>] [--kodi-notification] [--email-notification] [--schedule] [--daemon] [--version]", formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=50))

    # add argparse command line flags
    commandline_parser.add_argument(u"--config", metavar=u"<path>", help=u"specify path for config file e.g. --config /opt/triggerdockerbuild/config/")
    commandline_parser.add_argument(u"--logs", metavar=u"<path>", help=u"specify path for log files e.g. --logs /opt/triggerdockerbuild/logs/")
    commandline_parser.add_argument(u"--kodi-password", metavar=u"<password>", help=u"specify the password to access kodi e.g. --kodi-password foo")
    commandline_parser.add_argument(u"--email-to", metavar=u"<email address>", help=u"specify the email address to send email notifications to e.g. --email-to foo@bar.com")
    commandline_parser.add_argument(u"--email-username", metavar=u"<username>", help=u"specify the email account username e.g. --email-username foo@bar.com")
    commandline_parser.add_argument(u"--email-password", metavar=u"<password>", help=u"specify the email account password e.g. --email-password foo")
    commandline_parser.add_argument(u"--target-access-token", metavar=u"<token>", help=u"specify the github personal access token e.g. --target-access-token 123456789")
    commandline_parser.add_argument(u"--kodi-notification", action=u"store_true", help=u"enable kodi notification e.g. --kodi-notification")
    commandline_parser.add_argument(u"--email-notification", action=u"store_true", help=u"enable email notification e.g. --email-notification")
    commandline_parser.add_argument(u"--pidfile", metavar=u"<path>", help=u"specify path to pidfile e.g. --pid /var/run/triggerdockerbuild/triggerdockerbuild.pid")
    commandline_parser.add_argument(u"--schedule", action=u"store_true", help=u"enable scheduling e.g. --schedule")
    commandline_parser.add_argument(u"--daemon", action=u"store_true", help=u"run as daemonized process e.g. --daemon")
    commandline_parser.add_argument(u"--version", action=u"version", version=version)

    # save arguments in dictionary
    args = vars(commandline_parser.parse_args())

    # set path to root folder for application
    app_root_dir = os.path.dirname(os.path.realpath(__file__))

    if not args["config"]:

        # set folder path for config files
        config_dir = os.path.join(app_root_dir, u"configs")
        config_dir = os.path.normpath(config_dir)

    else:

        config_dir = args["config"]

    # set path for config.ini file
    config_ini = os.path.join(config_dir, u"config.ini")

    # create config and logs paths if they dont exist
    if not os.path.exists(config_dir):

        os.makedirs(config_dir)

    # set path for configspec.ini file
    configspec_ini = os.path.join(app_root_dir, u"configs/configspec.ini")

    # create configobj instance, set config.ini file, set encoding and set configspec.ini file
    config_obj = configobj.ConfigObj(config_ini, list_values=False, write_empty_values=True, encoding='UTF-8', default_encoding='UTF-8', configspec=configspec_ini, unrepr=True)

    # create config.ini
    create_config()

    if not args["logs"]:

        # set folder path for log files
        logs_dir = os.path.join(app_root_dir, u"logs")
        logs_dir = os.path.normpath(logs_dir)

    else:

        logs_dir = args["logs"]

    # set path for log file
    app_log_file = os.path.join(logs_dir, u"app.log")

    if not os.path.exists(logs_dir):

        os.makedirs(logs_dir)

    # setup logging
    app_log = app_logging()
    app_logger_instance = app_log.get('logger')
    app_handler = app_log.get('handler')

    if args["email_notification"]:

        email_notification = args["email_notification"]

    elif config_obj["notification"]["email_notification"] is not None:

        email_notification = config_obj["notification"]["email_notification"]

    else:

        app_logger_instance.info(u"Email Notification is not defined via '--email-notification' or 'config.ini', assuming True")
        email_notification = False

    if args["email_to"]:

        email_to = args["email_to"]

    elif config_obj["notification"]["email_to"] is not None:

        email_to = config_obj["notification"]["email_to"]

    else:

        app_logger_instance.info(u"Email To is not defined via '--email-to' or 'config.ini', setting Email Notification to false")
        email_notification = False

    if args["email_username"]:

        email_username = args["email_username"]

    elif config_obj["notification"]["email_username"] is not None:

        email_username = config_obj["notification"]["email_username"]

    else:

        app_logger_instance.info(u"Email Username is not defined via '--email-username' or 'config.ini', setting Email Notification to false")
        email_notification = False

    if args["email_password"]:

        email_password = args["email_password"]

    elif config_obj["notification"]["email_password"] is not None:

        email_password = config_obj["notification"]["email_password"]

    else:

        app_logger_instance.info(u"Email Password  is not defined via '--email-password' or 'config.ini', setting Email Notification to false")
        email_notification = False

    if args["kodi_notification"]:

        kodi_notification = args["kodi_notification"]

    elif config_obj["notification"]["kodi_notification"] is not None:

        kodi_notification = config_obj["notification"]["kodi_notification"]

    else:

        app_logger_instance.info(u"Kodi Password is not defined via '--kodi-password' or 'config.ini', setting Kodi Notification to false")
        kodi_notification = False

    if args["kodi_password"]:

        kodi_password = args["kodi_password"]

    elif config_obj["notification"]["kodi_password"] is not None:

        kodi_password = config_obj["notification"]["kodi_password"]

    else:

        app_logger_instance.info(u"Kodi Password is not defined via '--kodi-password' or 'config.ini', setting Kodi Notification to false")
        kodi_notification = False

    if args["target_access_token"]:

        target_access_token = args["target_access_token"]

    elif config_obj["general"]["target_access_token"] is not None:

        target_access_token = config_obj["general"]["target_access_token"]

    else:

        app_logger_instance.warning(u"Target Access Token is not defined via '--target-access-token' or 'config.ini', exiting script...")
        exit(1)

    # check os is not windows and then run main process as daemonized process
    if args["daemon"] is True and os.name != "nt":

        app_logger_instance.info(u"Running as a daemonized process...")

        # specify the logging handler as an exclusion to the daemon, to prevent its output being closed
        daemon_context = daemon.DaemonContext()
        daemon_context.files_preserve = [app_handler.stream]
        daemon_context.open()

    else:

        app_logger_instance.info(u"Running as a foreground process...")

    if args["schedule"] is True:

        app_logger_instance.info(u"Running via schedule...")
        scheduler_start()

    else:

        app_logger_instance.info(u"Running on demand...")
        ondemand_start()
