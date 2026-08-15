import argparse
import datetime
import html as _html  # aliased because notification_email uses 'html' as local var
import json
import logging
import logging.handlers
import os
import re
import signal
import sys
import time

import configobj
import daemon
import kodijson
import requests
import schedule
import urllib3
import validate
import yagmail

signal.signal(signal.SIGINT, signal.default_int_handler)  # ensure we correctly handle all keyboard interrupts

# TODO change input to functions as dictionary
# TODO change functions to **kwargs and use .get() to get value (will be none if not fund)
# TODO change return for function to dictionary

# Persistent notification state — survives between scheduler invocations while the process is running.
# Keyed by site_name (str). Each entry tracks whether a site was last known down and when the most
# recent notification was sent, so we only alert on state transitions and suppress repeat emails.
_site_down_state: dict = {}

# Per-site app-failure counters — also module-level so they accumulate across scheduler runs.
# Keyed by site_name (str); reset to 0 when any app for that site succeeds.
_app_down_counters: dict = {}

# Whether to verify TLS certificates on HTTPS requests. Defaults to True (secure).
# Set to False in config.ini only when behind an SSL-inspection proxy that uses
# self-signed certificates (e.g. corporate MITM).
verify_ssl = True


def _silence_tls_warnings(verify_ssl_enabled):
    """Suppress urllib3's InsecureRequestWarning when TLS verification is off.

    When the user explicitly sets verify_ssl = False (e.g. behind an
    SSL-inspection proxy), the resulting InsecureRequestWarning for every HTTPS
    request is expected noise. Keep warnings visible whenever verification is
    enabled (the secure default) so TLS problems are never hidden.
    """
    if not verify_ssl_enabled:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def create_config():

    validator = validate.Validator()
    config_obj.validate(validator, copy=True)
    config_obj.filename = config_ini
    config_obj.write()


def time_check(current_time, grace_period_mins, source_version_change_datetime):

    # compare difference between local date/time and trigger date/time to produce timedelta
    time_delta = current_time - source_version_change_datetime
    app_logger_instance.debug("Time delta object is %s" % time_delta)

    # turn timedelta object into minutes
    time_delta_secs = datetime.timedelta.total_seconds(time_delta)
    time_delta_mins = int(time_delta_secs) / 60

    grace_period_mins_int = int(grace_period_mins)

    # check if time_delta is greater than or equal to grace_period_mins
    if time_delta_mins >= grace_period_mins_int:
        app_logger_instance.info(
            "Time since last update (%s mins) >= to grace period (%s mins)" % (time_delta_mins, grace_period_mins)
        )
        return True

    else:
        app_logger_instance.info(
            "Time since last update (%s mins) < grace period (%s mins)" % (time_delta_mins, grace_period_mins)
        )
        return False


def _resolve_log_level(log_level):
    """Map a (case-insensitive) log level name to a logging module level.

    Unknown values default to WARNING.
    """
    levels = {
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "DEBUG": logging.DEBUG,
    }
    return levels.get(str(log_level).upper(), logging.WARNING)


def app_logging():

    # read log levels
    log_level = config_obj["general"]["log_level"]

    # setup formatting for log messages
    app_formatter = logging.Formatter(
        "%(asctime)s %(threadName)s %(module)s %(funcName)s :: [%(levelname)s] %(message)s"
    )

    # setup logger for app
    app_logger = logging.getLogger("app")

    # add rotating log handler
    app_rotatingfilehandler = logging.handlers.RotatingFileHandler(
        app_log_file, "a", maxBytes=10485760, backupCount=3, encoding="utf-8"
    )

    # set formatter for app
    app_rotatingfilehandler.setFormatter(app_formatter)

    # add the log message handler to the logger
    app_logger.addHandler(app_rotatingfilehandler)

    # set level of logging from config (case-insensitive, defaults to WARNING)
    resolved_level = _resolve_log_level(log_level)
    app_logger.setLevel(resolved_level)

    if resolved_level == logging.WARNING and str(log_level).upper() not in (
        "INFO",
        "WARNING",
        "ERROR",
        "DEBUG",
    ):
        app_logger.warning("Unrecognised log level '%s', defaulting to WARNING" % log_level)

    # setup logging to console
    console_streamhandler = logging.StreamHandler()

    # set formatter for console
    console_streamhandler.setFormatter(app_formatter)

    # add handler for formatter to the console
    app_logger.addHandler(console_streamhandler)

    # set level of logging from config for console
    console_streamhandler.setLevel(resolved_level)

    return {"logger": app_logger, "handler": app_rotatingfilehandler}


def _build_email_content(
    msg_type,
    action,
    source_site_name,
    source_repo_name,
    source_app_name,
    source_site_url,
    error_msg,
    previous_version,
    current_version,
    target_repo_name,
):
    """Build the (subject, html_body) for a notification email."""
    if msg_type == "site_error":
        subject = "%s - %s" % (source_site_name, msg_type)
        html = """
        <b>Source Site Name:</b> %s<br>
        <b>Source Site URL:</b>  <a href="%s">%s</a><br>
        <b>Error Message:</b> %s
        """ % (source_site_name, source_site_url, source_site_name, error_msg)
        return subject, html

    if msg_type == "site_recovered":
        subject = "%s - site recovered" % source_site_name
        html = """
        <b>Source Site Name:</b> %s<br>
        <b>Source Site URL:</b>  <a href="%s">%s</a><br>
        <b>Message:</b> %s
        """ % (source_site_name, source_site_url, source_site_name, error_msg)
        return subject, html

    if msg_type in ("config_error", "app_error"):
        subject = "%s - %s" % (source_app_name, msg_type)
        html = """
        <b>Source Site Name:</b> %s<br>
        <b>Source Repository:</b> %s<br>
        <b>Source Site URL:</b>  <a href="%s">%s</a><br>
        <b>Error Message:</b> %s
        """ % (source_site_name, source_repo_name, source_site_url, source_app_name, error_msg)
        return subject, html

    # default: trigger/notify version-change email
    target_repo_owner = config_obj["general"]["target_repo_owner"]
    dockerhub_build_details = "https://hub.docker.com/r/%s/%s/tags?page=1&ordering=last_updated&name=latest" % (
        target_repo_owner,
        target_repo_name,
    )
    github_action_details = "https://github.com/%s/%s/actions" % (target_repo_owner, target_repo_name)
    github_ghcr_details = "https://github.com/users/%s/packages/container/package/%s" % (
        target_repo_owner,
        target_repo_name,
    )
    subject = "%s [%s] - updated to %s" % (source_app_name, action, current_version)
    html = """
    <b>Action:</b> %s<br>
    <b>Previous Version:</b> %s<br>
    <b>Current Version:</b> %s<br>
    <b>Source Site Name:</b> %s<br>
    <b>Source Repository:</b> %s<br>
    <b>Source Site URL:</b>  <a href="%s">%s</a>
    """ % (
        action,
        previous_version,
        current_version,
        source_site_name,
        source_repo_name,
        source_site_url,
        source_app_name,
    )
    if action == "trigger":
        html += """
        <b>Target Repository URL:</b> <a href="https://github.com/%s/%s">github repo</a><br>
        <b>Target Github Action URL:</b> <a href="%s">github workflow</a><br>
        <b>Target Github Container Registry URL:</b> <a href="%s">github registry</a><br>
        <b>Target Docker Hub Registry URL:</b> <a href="%s">dockerhub registry</a>
        """ % (
            target_repo_owner,
            target_repo_name,
            github_action_details,
            github_ghcr_details,
            dockerhub_build_details,
        )
    return subject, html


def _esc(value):
    """HTML-escape a value, treating None/empty as an empty string."""
    return _html.escape(value or "")


def notification_email(**kwargs):

    if not email_notification:
        app_logger_instance.info("Email notification not enabled")
        return 1

    # unpack arguments from dictionary and HTML-escape for safe email rendering
    action = kwargs.get("action")
    msg_type = kwargs.get("msg_type")
    error_msg = _esc(kwargs.get("error_msg"))
    source_app_name = _esc(kwargs.get("source_app_name"))
    source_repo_name = _esc(kwargs.get("source_repo_name"))
    source_site_name = _esc(kwargs.get("source_site_name"))
    # Fall back to placeholder when source_site_url is None so emails don't render 'None'.
    # Used in href attributes — HTML-escape with quote=True so a URL containing
    # '"' cannot break out of the attribute (HTML injection).
    source_site_url = _html.escape(kwargs.get("source_site_url") or "(unknown)", quote=True)
    target_repo_name = _esc(kwargs.get("target_repo_name"))
    previous_version = _esc(kwargs.get("previous_version"))
    current_version = _esc(kwargs.get("current_version"))

    subject, html = _build_email_content(
        msg_type,
        action,
        source_site_name,
        source_repo_name,
        source_app_name,
        source_site_url,
        error_msg,
        previous_version,
        current_version,
        target_repo_name,
    )

    try:
        yag = yagmail.SMTP(email_username, email_password)
        app_logger_instance.info("Sending email notification...")
        yag.send(to=email_to, subject=subject, contents=[html])
        return 0

    except Exception:
        app_logger_instance.warning("Failed to send E-Mail notification to %s" % email_to)
        return 1


# noinspection PyUnresolvedReferences
def notification_kodi(action, source_app_name, current_version):

    if not kodi_notification:
        app_logger_instance.info("Kodi notification not enabled")
        return 1

    # read kodi config
    kodi_username = config_obj["notification"]["kodi_username"]
    kodi_hostname = config_obj["notification"]["kodi_hostname"]
    kodi_port = config_obj["notification"]["kodi_port"]

    # construct login with custom credentials for rpc call
    kodi = kodijson.Kodi("http://%s:%s/jsonrpc" % (kodi_hostname, kodi_port), kodi_username, kodi_password)

    # send gui notification
    try:
        app_logger_instance.info("Sending kodi notification...")
        kodi.GUI.ShowNotification(
            {
                "title": "TriggerDockerBuild",
                "message": "%s [%s] - updated to %s" % (source_app_name, action, current_version),
            }
        )

    except Exception:
        app_logger_instance.warning(
            "Failed to send notification to Kodi instance at http://%s:%s/jsonrpc" % (kodi_hostname, kodi_port)
        )
        return 1


def _parse_http_kwargs(kwargs):
    """Validate and extract http_client arguments.

    Returns a dict of parsed args, or None if a required argument is missing
    or invalid (a warning is logged in that case).
    """
    if kwargs is None:
        app_logger_instance.warning("No keyword args sent to function, exiting function...")
        return None

    url = kwargs.get("url")
    if not url:
        app_logger_instance.warning("No URL sent to function, exiting function...")
        return None

    user_agent = kwargs.get("user_agent")
    if not user_agent:
        app_logger_instance.warning("No User Agent sent to function, exiting function...")
        return None

    request_type = kwargs.get("request_type")
    if request_type not in ("get", "put", "post"):
        app_logger_instance.warning("Invalid or missing request type '%s', exiting function..." % request_type)
        return None

    return {
        "url": url,
        "user_agent": user_agent,
        "request_type": request_type,
        "auth": kwargs.get("auth"),
        "additional_header": kwargs.get("additional_header"),
        "data_payload": kwargs.get("data_payload"),
        "json_payload": kwargs.get("json_payload"),
    }


def _check_http_status(status_code, url, content):
    """Raise HTTPError for non-2xx responses, logging a specific message."""
    if 200 <= status_code <= 299:
        return

    if status_code == 401:
        message = "The status code %s indicates unauthorised access for %s, error is %s"
    elif status_code == 404:
        message = "The status code %s indicates the requested resource could not be found  for %s, error is %s"
    elif status_code == 422:
        message = (
            "The status code %s indicates a request was well-formed but was unable "
            "to be followed due to semantic errors for %s, error is %s"
        )
    else:
        message = "The status code %s indicates an unexpected error for %s, error is %s"

    app_logger_instance.warning(message % (status_code, url, content))
    raise requests.exceptions.HTTPError(status_code, url, content)


def _execute_http_request(
    session, url, user_agent, request_type, auth, additional_header, data_payload, json_payload, verify_ssl
):
    """Build the request and execute it, retrying transient 5xx errors.

    Returns (status_code, content).
    """
    connect_timeout = 60.0
    read_timeout = 60.0

    requests_data_dict = {
        "url": url,
        "timeout": (connect_timeout, read_timeout),
        "allow_redirects": True,
        "verify": verify_ssl,
    }

    session.headers.update({"Accept-encoding": "gzip", "User-Agent": user_agent})

    if additional_header:
        session.headers.update(additional_header)

    if auth:
        session.auth = auth

    if request_type in ("put", "post"):
        if json_payload is not None:
            # requests json= kwarg sets Content-Type: application/json
            requests_data_dict.update({"json": json_payload})
        else:
            requests_data_dict.update({"data": data_payload})

    request_method = getattr(session, request_type)

    transient_statuses = (502, 503, 504)
    max_attempts = 3
    retry_delay_secs = 5

    for attempt in range(max_attempts):
        response = request_method(**requests_data_dict)
        status_code = response.status_code
        content = response.content

        if status_code in transient_statuses and attempt < max_attempts - 1:
            app_logger_instance.warning(
                "Transient HTTP status %s from %s, retrying (%d/%d)..." % (status_code, url, attempt + 1, max_attempts)
            )
            time.sleep(retry_delay_secs)
        else:
            return status_code, content

    return status_code, content


def http_client(**kwargs):

    parsed = _parse_http_kwargs(kwargs)
    if parsed is None:
        return 1, None, None

    url = parsed["url"]

    # use a session instance to customize how "requests" handles making http requests
    session = requests.Session()

    # Default to verifying SSL certificates. Users behind proxies with custom CA bundles
    # can set REQUESTS_CA_BUNDLE or SSL_CERT_FILE environment variables instead of disabling.
    # Set verify_ssl = False in config.ini to disable verification entirely
    # (only for environments with self-signed certs from SSL-inspection proxies).
    effective_verify_ssl = kwargs.get("verify_ssl", globals().get("verify_ssl", True))

    status_code = None

    try:
        status_code, content = _execute_http_request(
            session,
            url,
            parsed["user_agent"],
            parsed["request_type"],
            parsed["auth"],
            parsed["additional_header"],
            parsed["data_payload"],
            parsed["json_payload"],
            effective_verify_ssl,
        )
        _check_http_status(status_code, url, content)

    except requests.exceptions.HTTPError as content:
        # HTTP error already logged by _check_http_status
        return 1, status_code, content

    except requests.exceptions.RequestException as content:
        # All remaining requests exceptions (timeouts, connection errors, redirects)
        app_logger_instance.warning("%s for URL %s with error %s" % (type(content).__name__, url, content))
        return 1, status_code, content

    app_logger_instance.info("The status code %s indicates a successful request for %s" % (status_code, url))
    return 0, status_code, content


def github_create_release(current_version, target_repo_branch, target_repo_owner, target_repo_name, user_agent):
    """Create a GitHub release via the REST API.

    Sends JSON via requests json= kwarg which automatically sets
    Content-Type: application/json (required by the GitHub API).

    Returns (return_code, status_code, content).
    """

    # remove illegal characters from version (github does not allow certain chars for release name)
    current_version = re.sub(r":", r".", current_version)

    app_logger_instance.info("Creating Release on GitHub for version %s..." % current_version)

    github_tag_name = "%s-01" % current_version
    github_release_name = "API/URL triggered release"
    github_release_body = github_tag_name
    request_type = "post"
    http_url = "https://api.github.com/repos/%s/%s/releases" % (target_repo_owner, target_repo_name)
    # JSON dict sent via requests json= kwarg → Content-Type: application/json
    # (required by the GitHub API; data= without the header causes 422 errors)
    json_payload = {
        "tag_name": github_tag_name,
        "target_commitish": target_repo_branch,
        "name": github_release_name,
        "body": github_release_body,
        "draft": False,
        "prerelease": False,
    }

    # process post request
    return_code, status_code, content = http_client(
        url=http_url,
        user_agent=user_agent,
        additional_header={"Authorization": "token %s" % target_access_token},
        request_type=request_type,
        json_payload=json_payload,
    )

    # GitHub returns a misleading 422 "tag_name is not a valid tag" when the
    # configured target_repo_branch does not exist on the target repo (e.g.
    # 'master' when the repo only has 'main'). Fall back to an empty
    # target_commitish, which GitHub auto-maps to the repo's default branch.
    if return_code != 0 and status_code == 422:
        app_logger_instance.warning(
            "Release creation failed with 422 for branch '%s', "
            "retrying with the repo's default branch..." % target_repo_branch
        )
        # Build a fresh payload so the fallback call cannot share mutation
        # with the first attempt's recorded request.
        fallback_payload = dict(json_payload)
        fallback_payload["target_commitish"] = ""
        return_code, status_code, content = http_client(
            url=http_url,
            user_agent=user_agent,
            additional_header={"Authorization": "token %s" % target_access_token},
            request_type=request_type,
            json_payload=fallback_payload,
        )

    return return_code, status_code, content


def check_site(**kwargs):

    # unpack arguments from dictionary
    url = kwargs.get("url")
    user_agent = kwargs.get("user_agent")
    site_name = kwargs.get("site_name")
    # Hours to wait before sending a "still down" reminder while a site remains degraded.
    # Prevents a flood of repeat emails across scheduler runs during a prolonged outage.
    notification_cooldown_hours = kwargs.get("notification_cooldown_hours", 4)

    request_type = "get"

    # set number of retries and set default site_down boolean
    retries = 10
    sleep_secs = 60
    site_down = True

    while True:
        # Only send GitHub PAT to GitHub — not to third-party sites like GitLab, PyPI, AOR, or AUR.
        if site_name.lower() == "github":
            auth_header = {"Authorization": "token %s" % target_access_token}
        else:
            auth_header = None

        # download json content
        return_code, status_code, content = http_client(
            url=url, user_agent=user_agent, additional_header=auth_header, request_type=request_type
        )

        if return_code == 0:
            site_down = False
            app_logger_instance.debug(f"'{site_name}' site operational for '{url}'")
            break
        else:
            app_logger_instance.info(
                f"Having issues connecting to '{site_name}' for '{url}', retrying in '{sleep_secs}' seconds..."
            )
            time.sleep(sleep_secs)
            retries = retries - 1

        if retries <= 0:
            app_logger_instance.warning(f"'{site_name}' site down for '{url}'")
            break

    _handle_site_state(site_name, url, site_down, notification_cooldown_hours)

    return site_down


def _handle_site_state(site_name, url, site_down, notification_cooldown_hours):
    """Send site_error/site_recovered notifications on state transitions.

    Updates _site_down_state in place. Sends a first-time alert on UP→DOWN,
    re-notifies after the cooldown elapses, and sends a recovery email on DOWN→UP.
    """
    previous_state = _site_down_state.get(site_name, {"is_down": False, "notified_at": None})
    was_down = previous_state["is_down"]
    last_notified = previous_state.get("notified_at")

    if not site_down:
        if was_down:
            recovery_msg = f"{site_name} site has recovered - '{url}'"
            notification_email(
                msg_type="site_recovered", error_msg=recovery_msg, source_site_name=site_name, source_site_url=url
            )
            app_logger_instance.info(recovery_msg)
        _site_down_state[site_name] = {"is_down": False, "notified_at": None}
        return

    if not was_down:
        # Transition: UP → DOWN — send first-time alert and record state
        error_msg = f"{site_name} site down - '{url}'"
        notification_email(msg_type="site_error", error_msg=error_msg, source_site_name=site_name, source_site_url=url)
        app_logger_instance.warning(error_msg)
        _site_down_state[site_name] = {"is_down": True, "notified_at": datetime.datetime.now(datetime.UTC)}
        return

    # Site was already known down — only re-notify after the cooldown period elapses
    now = datetime.datetime.now(datetime.UTC)
    hours_since_notif = ((now - last_notified).total_seconds() / 3600) if last_notified else notification_cooldown_hours

    if hours_since_notif >= notification_cooldown_hours:
        error_msg = f"{site_name} site still down - '{url}' (ongoing issue, last notified {hours_since_notif:.1f}h ago)"
        notification_email(msg_type="site_error", error_msg=error_msg, source_site_name=site_name, source_site_url=url)
        app_logger_instance.warning(error_msg)
        _site_down_state[site_name] = {"is_down": True, "notified_at": now}
    else:
        app_logger_instance.info(
            f"'{site_name}' already known down, suppressing repeat notification "
            f"(last notified {hours_since_notif:.1f}h ago, cooldown {notification_cooldown_hours}h)"
        )


def github_target_last_release_date(target_repo_owner, target_repo_name, user_agent):

    github_query_type = "releases/latest"
    json_query = "published_at"

    # construct url to github rest api
    url = "https://api.github.com/repos/%s/%s/%s" % (target_repo_owner, target_repo_name, github_query_type)
    request_type = "get"

    # download json content
    return_code, status_code, content = http_client(
        url=url,
        user_agent=user_agent,
        additional_header={"Authorization": "token %s" % target_access_token},
        request_type=request_type,
    )

    if return_code == 0:
        try:
            content = json.loads(content)

        except (ValueError, TypeError, KeyError):
            app_logger_instance.info("Problem loading json from %s" % url)
            return 1, None

    else:
        app_logger_instance.info("Problem downloading json content from %s" % url)
        return 1, None

    try:
        # get release date from json
        target_last_release_date = content["%s" % json_query]

    except (IndexError, KeyError):
        app_logger_instance.info("Problem parsing json from %s, skipping to next iteration..." % url)
        return 1, None

    # convert the following then compare against throttle days value "2020-04-15T21:53:20Z"
    return 0, target_last_release_date


def _github_query_mapping(source_query_type):
    """Map a github source_query_type to (github_query_type, json_query).

    Returns (None, None) for an unknown query type.
    """
    mapping = {
        "tag": ("tags", "name"),
        "pre-release": ("releases", "tag_name"),
        "release": ("releases/latest", "tag_name"),
        "branch": ("commits", "sha"),
    }
    return mapping.get((source_query_type or "").lower(), (None, None))


def github_apps(source_app_name, source_query_type, source_repo_name, user_agent, source_branch_name):

    # certain github repos do not have releases, only tags, thus we need to account for these differently
    github_query_type, json_query = _github_query_mapping(source_query_type)

    if github_query_type is None:
        app_logger_instance.warning(
            "source_query_type '%s' is not valid, skipping to next iteration..." % source_query_type
        )
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
    return_code, status_code, content = http_client(
        url=url,
        user_agent=user_agent,
        additional_header={"Authorization": "token %s" % target_access_token},
        request_type=request_type,
    )

    if return_code == 0:
        try:
            content = json.loads(content)

        except (ValueError, TypeError, KeyError):
            app_logger_instance.info("Problem loading json from %s" % url)
            return None, source_site_url

    else:
        app_logger_instance.info("Problem downloading json content from %s" % url)
        return None, source_site_url

    try:
        # releases/latest returns a dict; tags/commits/releases return a list
        if github_query_type == "releases/latest":
            current_version = content["%s" % json_query]
        else:
            current_version = content[0]["%s" % json_query]

    except (IndexError, KeyError):
        app_logger_instance.warning("Problem parsing json from %s, skipping to next iteration..." % url)
        return None, source_site_url

    if source_query_type.lower() == "branch":
        source_site_url = "%s/%s" % (source_site_url, source_branch_name)

    return current_version, source_site_url


def gitlab_apps(
    source_app_name, source_repo_name, source_project_id, source_branch_name, source_query_type, user_agent
):

    # use gitlab rest api
    url = "https://gitlab.com/api/v4/projects/%s/repository/commits/%s" % (source_project_id, source_branch_name)

    # construct url for package details
    source_site_url = "https://gitlab.com/%s/%s" % (source_repo_name, source_app_name)

    request_type = "get"

    if source_query_type.lower() == "branch":
        json_query = "id"

    else:
        app_logger_instance.warning(
            "source_query_type '%s' is not valid, skipping to next iteration..." % source_query_type.lower()
        )
        return None, source_site_url

    # download webpage content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent, request_type=request_type)

    if return_code == 0:
        try:
            # decode json
            content = json.loads(content)

        except (ValueError, TypeError, KeyError, IndexError):
            app_logger_instance.info("Problem loading json from %s" % url)
            return None, source_site_url

    else:
        app_logger_instance.info("Problem downloading json content from %s" % url)
        return None, source_site_url

    try:
        # construct app version
        current_version = content["%s" % json_query]

    except (ValueError, TypeError, KeyError, IndexError):
        app_logger_instance.info("Problem parsing json from %s, skipping to next iteration..." % url)
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
            app_logger_instance.info("Problem loading json from %s" % url)
            return None, source_site_url

    else:
        app_logger_instance.info("Problem downloading json content from %s" % url)
        return None, source_site_url

    try:
        current_version = content["info"]["version"]

    except (KeyError, TypeError):
        app_logger_instance.info("Problem extracting version from json for %s, skipping to next iteration..." % url)
        return None, source_site_url

    return current_version, source_site_url


def aor_apps(source_app_name, user_agent):

    # use aor unofficial api to get app release info
    url = "https://archlinux.org/packages/search/json/?q=%s" % source_app_name
    request_type = "get"

    # construct url for package details
    source_site_url = f"https://archlinux.org/packages/?sort=&q={source_app_name}&maintainer=&flagged="

    # download webpage content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent, request_type=request_type)

    if return_code != 0:
        app_logger_instance.info("Problem downloading json content from %s" % url)
        return None, source_site_url

    try:
        # decode json
        content = json.loads(content)

        # filter python objects with list comprehension to prevent fuzzy mismatch
        content = [x for x in content["results"] if x["pkgname"] == source_app_name]

        # get package version and release number from json
        pkgver = content[0]["pkgver"]
        pkgrel = content[0]["pkgrel"]

        # construct app version
        current_version = "%s-%s" % (pkgver, pkgrel)

    except (ValueError, TypeError, KeyError, IndexError):
        app_logger_instance.info("Problem loading or parsing json from %s, skipping to next iteration..." % url)
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
            app_logger_instance.info("Problem loading json from %s" % url)
            return None, source_site_url

    else:
        app_logger_instance.info("Problem downloading json content from %s" % url)
        return None, source_site_url

    try:
        # get app version from json
        current_version = content["results"][0]["Version"]

    except (IndexError, KeyError):
        app_logger_instance.info("Problem parsing json from %s, skipping to next iteration..." % url)
        return None, source_site_url

    return current_version, source_site_url


APP_DOWN_COUNTER_MAX = 3  # max consecutive failed-app-detection emails before suppressing


def _handle_app_fetch(current_version, site_key, source_site_name, source_app_name, source_repo_name, source_site_url):
    """Handle the common 'fetch succeeded or failed' pattern for a single app.

    On failure: increments the persistent counter, sends an app_error email if the
    count is at or below APP_DOWN_COUNTER_MAX, or suppresses notification.
    On success: resets this app's failure counter.

    Args:
        current_version: version returned by the site fetch, or None on failure
        site_key: "site:app" counter key
        source_site_name: name of the source site
        source_app_name: application name
        source_repo_name: repository name
        source_site_url: URL of the source site

    Returns:
        True if processing should continue (fetch succeeded), False to skip.
    """
    if current_version is None:
        _app_down_counters[site_key] = _app_down_counters.get(site_key, 0) + 1
        error_msg = (
            f"Unable to connect to site '{source_site_name}' "
            f"for application '{source_app_name}', skipping to next iteration..."
        )

        if _app_down_counters[site_key] <= APP_DOWN_COUNTER_MAX:
            notification_email(
                msg_type="app_error",
                error_msg=error_msg,
                source_site_name=source_site_name,
                source_repo_name=source_repo_name,
                source_app_name=source_app_name,
                source_site_url=source_site_url,
            )
        else:
            app_logger_instance.info(
                f"Number of failed downloads for site '{source_site_name}' "
                f"has exceeded '{APP_DOWN_COUNTER_MAX}', skipping notifications"
            )

        app_logger_instance.warning(error_msg)
        return False

    # Fetch succeeded — reset this app's counter
    _app_down_counters.pop(site_key, None)
    return True


def _notify_app_error(site_key, source_site_name, source_app_name, source_repo_name, source_site_url, error_msg):
    """Send an app_error notification bounded by APP_DOWN_COUNTER_MAX.

    Used by the regex/minecraft branches that do not follow the
    current_version=None pattern of _handle_app_fetch. Increments the persistent
    failure counter and only emails while the count is at or below the max, so a
    prolonged upstream outage does not produce one email per scheduler run.
    """
    _app_down_counters[site_key] = _app_down_counters.get(site_key, 0) + 1

    if _app_down_counters[site_key] <= APP_DOWN_COUNTER_MAX:
        notification_email(
            msg_type="app_error",
            error_msg=error_msg,
            source_site_name=source_site_name,
            source_repo_name=source_repo_name,
            source_app_name=source_app_name,
            source_site_url=source_site_url,
        )
    else:
        app_logger_instance.info(
            f"Number of failed downloads for site '{source_site_name}' "
            f"has exceeded '{APP_DOWN_COUNTER_MAX}', skipping notifications"
        )

    app_logger_instance.warning(error_msg)


def _handle_version_change(
    site_item,
    source_site_name,
    source_app_name,
    source_repo_name,
    source_site_url,
    target_repo_name,
    target_repo_branch,
    target_repo_owner,
    action,
    current_version,
    previous_version,
    grace_period_mins,
    target_release_days,
    source_version_change_datetime,
    user_agent_chrome,
):
    """Handle a detected version change for one app.

    Performs the trigger (with grace-period and release-days throttling) or
    notify action, then sends the notification and updates config. Returns True
    if the caller should skip the remainder of the current site iteration.
    """
    if action == "trigger":
        if _trigger_release(
            site_item,
            source_site_name,
            source_app_name,
            target_repo_name,
            target_repo_branch,
            target_repo_owner,
            current_version,
            previous_version,
            grace_period_mins,
            target_release_days,
            source_version_change_datetime,
            user_agent_chrome,
        ):
            return True

    elif action == "notify":
        app_logger_instance.info(
            "Previous version %s and current version %s are different" % (previous_version, current_version)
        )

    app_logger_instance.debug("Writing current version %s to config.ini" % current_version)
    config_obj["results"]["%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)] = (
        current_version
    )
    config_obj.write()

    notification_email(
        action=action,
        source_app_name=source_app_name,
        source_repo_name=source_repo_name,
        source_site_name=source_site_name,
        source_site_url=source_site_url,
        target_repo_name=target_repo_name,
        previous_version=previous_version,
        current_version=current_version,
    )

    notification_kodi(action, source_app_name, current_version)

    return False


def _throttle_by_grace_period(
    site_item,
    source_app_name,
    grace_period_mins,
    source_version_change_datetime,
    current_datetime_object,
    current_datetime_str,
):
    """Return True if the trigger should be skipped due to the grace period."""
    if not grace_period_mins:
        return False

    if source_version_change_datetime is None:
        app_logger_instance.debug("Trigger datetime not defined in config.ini, creating from current datetime")
        site_item["source_version_change_datetime"] = current_datetime_str
        config_obj.write()
        return True

    source_version_change_datetime_object = datetime.datetime.strptime(
        source_version_change_datetime, "%Y-%m-%d %H:%M:%S"
    )

    if not time_check(current_datetime_object, grace_period_mins, source_version_change_datetime_object):
        app_logger_instance.info(
            "Source version change for app '%s' is less than '%s' mins ago, "
            "skipping to next iteration..." % (source_app_name, grace_period_mins)
        )
        return True

    app_logger_instance.info(
        "Source version change for app '%s' is >= '%s' mins ago, proceeding..." % (source_app_name, grace_period_mins)
    )
    return False


def _throttle_by_release_days(
    target_repo_owner, target_repo_name, user_agent_chrome, target_release_days, current_datetime_object
):
    """Return True if the trigger should be skipped due to release-day throttling."""
    if not target_release_days:
        return False

    return_code, last_release_date = github_target_last_release_date(
        target_repo_owner, target_repo_name, user_agent_chrome
    )

    if return_code != 0:
        app_logger_instance.warning(
            "Unable to identify target release date for repo '%s', skipping to next iteration..." % target_repo_name
        )
        return True

    target_release_date_object = datetime.datetime.strptime(last_release_date, "%Y-%m-%dT%H:%M:%SZ")
    target_time_delta_days = (current_datetime_object - target_release_date_object).days
    app_logger_instance.debug("Minimum days between target releases is '%s' days" % target_release_days)
    app_logger_instance.debug("Last target release was '%s' days ago" % target_time_delta_days)

    if int(target_time_delta_days) < int(target_release_days):
        app_logger_instance.info(
            "Last target release date for app '%s' is less than '%s' days ago, "
            "skipping to next iteration..." % (target_repo_name, target_release_days)
        )
        return True

    app_logger_instance.info(
        "Last target release date for app '%s' is >= '%s' days ago, proceeding..."
        % (target_repo_name, target_release_days)
    )
    return False


def _trigger_release(
    site_item,
    source_site_name,
    source_app_name,
    target_repo_name,
    target_repo_branch,
    target_repo_owner,
    current_version,
    previous_version,
    grace_period_mins,
    target_release_days,
    source_version_change_datetime,
    user_agent_chrome,
):
    """Create the GitHub release for a detected version change.

    Applies grace-period and release-days throttling first. Returns True if the
    caller should skip the remainder of the current site iteration.
    """
    current_datetime_object = datetime.datetime.now()
    current_datetime_str = current_datetime_object.strftime("%Y-%m-%d %H:%M:%S")

    if _throttle_by_grace_period(
        site_item,
        source_app_name,
        grace_period_mins,
        source_version_change_datetime,
        current_datetime_object,
        current_datetime_str,
    ):
        return True

    if _throttle_by_release_days(
        target_repo_owner, target_repo_name, user_agent_chrome, target_release_days, current_datetime_object
    ):
        return True

    app_logger_instance.info(
        "Previous version %s and current version %s are different, "
        "triggering a docker hub build (via github tag)..." % (previous_version, current_version)
    )
    return_code, status_code, content = github_create_release(
        current_version, target_repo_branch, target_repo_owner, target_repo_name, user_agent_chrome
    )

    if status_code == 201:
        app_logger_instance.info(
            "Setting previous version %s to the same as current version %s after successful build"
            % (previous_version, current_version)
        )
    else:
        # TODO this is a hack to work around the fact we have converted dict to keyword args
        regex_code = r'(?<="code":\s")[^"]+'
        try:
            code = (re.search(regex_code, str(content))).group(0)
            if code.lower() == "already_exists":
                app_logger_instance.warning(
                    "Problem creating GitHub release as it already exists for '%s/%s', "
                    "overwriting current version and skipping to next iteration..."
                    % (target_repo_owner, target_repo_name)
                )
                app_logger_instance.debug("Writing current version %s to config.ini" % current_version)
                config_obj["results"][
                    "%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)
                ] = current_version
                config_obj.write()
        except AttributeError:
            app_logger_instance.warning(
                "Problem creating GitHub release due to unknown error for '%s/%s', "
                "skipping to next iteration..." % (target_repo_owner, target_repo_name)
            )
        return True

    if source_version_change_datetime is not None:
        app_logger_instance.debug("Deleting 'source_version_change_datetime', used next time version change occurs")
        del site_item["source_version_change_datetime"]
        config_obj.write()

    app_logger_instance.debug("Creating 'target_trigger_datetime', used to track when trigger of docker build happened")
    site_item["target_trigger_datetime"] = current_datetime_str
    config_obj.write()

    return False


def _fetch_regex_version(source_app_name, source_site_name, source_repo_name, source_site_url, user_agent_chrome):
    """Fetch the current version for a regex-based (minecraft) app.

    Returns (current_version, source_site_url); current_version is None on
    failure (a bounded app_error notification has already been sent).
    """
    if source_app_name == "minecraftbedrock":
        return _fetch_bedrock_version(
            source_site_name, source_app_name, source_repo_name, source_site_url, user_agent_chrome
        )

    if source_app_name == "minecraftserver":
        return _fetch_minecraftserver_version(source_site_name, source_app_name, source_repo_name, user_agent_chrome)

    app_logger_instance.warning("Source site app %s unknown, skipping to next iteration..." % source_app_name)
    return None, source_site_url


def _fetch_bedrock_version(source_site_name, source_app_name, source_repo_name, source_site_url, user_agent_chrome):
    """Fetch the bedrock server version from the Mojang download-links API."""
    bedrock_unofficial_api = "https://net-secondary.web.minecraft-services.net/api/v1.0/download/links"
    return_code, status_code, content = http_client(
        url=bedrock_unofficial_api, user_agent=user_agent_chrome, request_type="get"
    )

    if return_code != 0:
        _notify_app_error(
            f"regex:{source_app_name}",
            source_site_name,
            source_app_name,
            source_repo_name,
            source_site_url,
            "Unable to get bedrock download links from API for app %s, skipping to next iteration..." % source_app_name,
        )
        return None, source_site_url

    try:
        api_data = json.loads(content)
        download_url = None
        for link_item in api_data["result"]["links"]:
            if link_item["downloadType"] == "serverBedrockLinux":
                download_url = link_item["downloadUrl"]
                break

        if not download_url:
            raise KeyError("serverBedrockLinux not found in API response")

        version_match = re.search(r"bedrock-server-(.*)\.zip", download_url)
        if not version_match:
            raise ValueError("Could not extract version from download URL")

        return version_match.group(1), source_site_url

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        _notify_app_error(
            f"regex:{source_app_name}",
            source_site_name,
            source_app_name,
            source_repo_name,
            source_site_url,
            "Unable to parse bedrock API response for app %s: %s" % (source_app_name, str(e)),
        )
        return None, source_site_url


def _fetch_minecraftserver_version(source_site_name, source_app_name, source_repo_name, user_agent_chrome):
    """Fetch the Java edition server version from the Mojang version manifest."""
    source_site_url = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
    return_code, status_code, content = http_client(
        url=source_site_url, user_agent=user_agent_chrome, request_type="get"
    )

    if return_code != 0:
        _notify_app_error(
            f"regex:{source_app_name}",
            source_site_name,
            source_app_name,
            source_repo_name,
            source_site_url,
            "Problem downloading version manifest for url '%s', skipping to next iteration..." % source_site_url,
        )
        return None, source_site_url

    try:
        current_version = json.loads(content)["latest"]["release"]
        return current_version, source_site_url

    except (json.JSONDecodeError, ValueError, IndexError, KeyError):
        _notify_app_error(
            f"regex:{source_app_name}",
            source_site_name,
            source_app_name,
            source_repo_name,
            source_site_url,
            "Unable to identify current release version for app '%s', ignoring..." % source_app_name,
        )
        return None, source_site_url


def monitor_sites():

    # read sites list from config
    config_site_list = config_obj["monitor_sites"]["site_list"]

    # Defensive: a legacy or malformed config may leave site_list as a plain
    # string (the old configspec default was a quoted string). Treat it as an
    # empty list rather than iterating string characters and crashing.
    if not isinstance(config_site_list, list):
        app_logger_instance.warning(
            "site_list in config.ini is not a list (got %s), no sites to monitor" % type(config_site_list).__name__
        )
        config_site_list = []

    target_repo_owner = config_obj["general"]["target_repo_owner"]

    # pretend to be windows 10 running chrome (required for minecraft bedrock)
    user_agent_chrome = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"
    )

    # check github api is operational
    url = "https://api.github.com"

    site_down_github = check_site(url=url, user_agent=user_agent_chrome, site_name="GitHub")

    # check gitlab rest api is operational
    url = "https://gitlab.com/api/v4/projects"

    site_down_gitlab = check_site(url=url, user_agent=user_agent_chrome, site_name="GitLab")

    # check pypi website is operational
    test_package = "requests"
    url = f"https://pypi.org/pypi/{test_package}/json"

    site_down_pypi = check_site(url=url, user_agent=user_agent_chrome, site_name="PyPi")

    # check aor site is operational
    test_package = "base"
    url = f"https://archlinux.org/packages/core/any/{test_package}/"

    site_down_aor = check_site(url=url, user_agent=user_agent_chrome, site_name="AOR")

    # check aur site is operational
    test_package = "yay"
    url = f"https://aur.archlinux.org/rpc/?v=5&type=info&arg[]={test_package}"

    site_down_aur = check_site(url=url, user_agent=user_agent_chrome, site_name="AUR")

    # set counter for number of failures to get app package details
    # These are module-level dicts (_app_down_counters) so they persist across scheduler runs.
    # Counters are keyed by "site_name:app_name" so a success for one app does not reset the
    # counter for a different app on the same site that is still failing.

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
        # Normalise to empty string so a missing source_query_type is handled
        # as "invalid" by the site functions instead of crashing on .lower().
        source_query_type = site_item.get("source_query_type") or ""
        grace_period_mins = site_item.get("grace_period_mins")
        source_version_change_datetime = site_item.get("source_version_change_datetime")
        action = site_item.get("action")

        # set default values in case they are not supplied
        source_site_url = None

        app_logger_instance.info("-------------------------------------")
        app_logger_instance.info("Processing started for application %s..." % source_app_name)

        if action != "notify":
            # if target branch not defined then send email notification and skip to next item
            if target_repo_branch is None:
                msg_type = "config_error"
                error_msg = (
                    "Target repo branch not defined for target repo '%s', skipping to next iteration..."
                    % target_repo_name
                )
                notification_email(
                    msg_type=msg_type,
                    error_msg=error_msg,
                    source_site_name=source_site_name,
                    source_repo_name=source_repo_name,
                    source_app_name=source_app_name,
                    source_site_url=source_site_url,
                )
                app_logger_instance.warning(error_msg)
                continue

        if source_site_name == "github":
            if site_down_github:
                app_logger_instance.warning(
                    "Site '%s' marked as down, skipping processing for application '%s'..."
                    % (source_site_name, source_app_name)
                )
                continue

            current_version, source_site_url = github_apps(
                source_app_name, source_query_type, source_repo_name, user_agent_chrome, source_branch_name
            )

            if not _handle_app_fetch(
                current_version,
                f"github:{source_app_name}",
                source_site_name,
                source_app_name,
                source_repo_name,
                source_site_url,
            ):
                continue

        elif source_site_name == "gitlab":
            if site_down_gitlab:
                app_logger_instance.warning(
                    "Site '%s' marked as down, skipping processing for application '%s'..."
                    % (source_site_name, source_app_name)
                )
                continue

            current_version, source_site_url = gitlab_apps(
                source_app_name,
                source_repo_name,
                source_project_id,
                source_branch_name,
                source_query_type,
                user_agent_chrome,
            )

            if not _handle_app_fetch(
                current_version,
                f"gitlab:{source_app_name}",
                source_site_name,
                source_app_name,
                source_repo_name,
                source_site_url,
            ):
                continue

        elif source_site_name == "pypi":
            if site_down_pypi:
                app_logger_instance.warning(
                    "Site '%s' marked as down, skipping processing for application '%s'..."
                    % (source_site_name, source_app_name)
                )
                continue

            current_version, source_site_url = pypi_apps(source_app_name, user_agent_chrome)

            if not _handle_app_fetch(
                current_version,
                f"pypi:{source_app_name}",
                source_site_name,
                source_app_name,
                source_repo_name,
                source_site_url,
            ):
                continue

        elif source_site_name == "aor":
            if site_down_aor:
                app_logger_instance.warning(
                    "Site '%s' marked as down, skipping processing for application '%s'..."
                    % (source_site_name, source_app_name)
                )
                continue

            # if grace period not defined then set to default value (required for aor)
            if grace_period_mins is None:
                grace_period_mins = 60

            current_version, source_site_url = aor_apps(source_app_name, user_agent_chrome)

            if not _handle_app_fetch(
                current_version,
                f"aor:{source_app_name}",
                source_site_name,
                source_app_name,
                source_repo_name,
                source_site_url,
            ):
                continue

        elif source_site_name == "aur":
            if site_down_aur:
                app_logger_instance.warning(
                    "Site '%s' marked as down, skipping processing for application '%s'..."
                    % (source_site_name, source_app_name)
                )
                continue

            current_version, source_site_url = aur_apps(source_app_name, user_agent_chrome)

            if not _handle_app_fetch(
                current_version,
                f"aur:{source_app_name}",
                source_site_name,
                source_app_name,
                source_repo_name,
                source_site_url,
            ):
                continue

        elif source_site_name == "regex":
            current_version, source_site_url = _fetch_regex_version(
                source_app_name, source_site_name, source_repo_name, source_site_url, user_agent_chrome
            )

            if current_version is None:
                continue

            # Successful regex fetch — reset this app's failure counter
            _app_down_counters.pop(f"regex:{source_app_name}", None)

        else:
            app_logger_instance.warning("Source site name %s unknown, skipping to next iteration..." % source_site_name)
            continue

        # write value for current match to config
        config_obj["results"]["%s_%s_%s_current_version" % (source_site_name, source_app_name, target_repo_name)] = (
            current_version
        )
        config_obj.write()

        try:
            # read value from previous match from config
            previous_version = config_obj["results"][
                "%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)
            ]

        except KeyError:
            app_logger_instance.info("No known previous version for app %s, assuming first run" % source_app_name)
            app_logger_instance.info(
                "Setting previous version to current version %s and going to next iteration" % current_version
            )
            config_obj["results"][
                "%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)
            ] = current_version
            config_obj.write()
            continue

        if previous_version != current_version:
            if _handle_version_change(
                site_item,
                source_site_name,
                source_app_name,
                source_repo_name,
                source_site_url,
                target_repo_name,
                target_repo_branch,
                target_repo_owner,
                action,
                current_version,
                previous_version,
                grace_period_mins,
                target_release_days,
                source_version_change_datetime,
                user_agent_chrome,
            ):
                continue

        else:
            app_logger_instance.info(
                "Previous version %s and current version %s match, nothing to do" % (previous_version, current_version)
            )

        app_logger_instance.info("Processing finished for application %s" % source_app_name)

    # write timestamp to config.ini
    config_obj["general"]["last_check"] = time.strftime("%c")
    config_obj.write()


def ondemand_start():

    app_logger_instance.info("Checking for version changes...")
    monitor_sites()


def scheduler_start():

    schedule_check_mins = config_obj["general"]["schedule_check_mins"]

    # now run monitor_sites function via scheduler
    schedule.every(schedule_check_mins).minutes.do(monitor_sites)

    while True:
        try:
            schedule.run_pending()
            # Sleep most of the interval to avoid flooding the log.
            # Wake every 30s to catch clock drift; schedule.run_pending() is cheap.
            sleep_secs = 30
            time.sleep(sleep_secs)

        except KeyboardInterrupt:
            app_logger_instance.info("Keyboard interrupt received, exiting script...")
            sys.exit()

        except Exception as e:
            app_logger_instance.warning("Unhandled exception in scheduler loop: %s" % e)
            app_logger_instance.debug("Exception details:", exc_info=True)


# required to prevent separate process from trying to load parent process
if __name__ == "__main__":
    version = "1.2.6"

    # custom argparse to redirect user to help if unknown argument specified
    class ArgparseCustom(argparse.ArgumentParser):
        def error(self, message):
            sys.stderr.write("error: %s\n" % message)
            self.print_help()
            sys.exit(2)

    # setup argparse description and usage, also increase spacing for help to 50
    commandline_parser = ArgparseCustom(
        prog="TriggerDockerBuild",
        description="%(prog)s " + version,
        usage=(
            "%(prog)s [--help] [--config <path>] [--logs <path>] [--kodi-password <password>] "
            "[--email-to <email address>] [--email-username <username>] "
            "[--email-password <password>] [--target-access-token <token>] [--pidfile <path>] "
            "[--kodi-notification] [--email-notification] [--schedule] [--daemon] [--version]"
        ),
        formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=50),
    )

    # add argparse command line flags
    commandline_parser.add_argument(
        "--config", metavar="<path>", help="specify path for config file e.g. --config /opt/triggerdockerbuild/config/"
    )
    commandline_parser.add_argument(
        "--logs", metavar="<path>", help="specify path for log files e.g. --logs /opt/triggerdockerbuild/logs/"
    )
    commandline_parser.add_argument(
        "--kodi-password", metavar="<password>", help="specify the password to access kodi e.g. --kodi-password foo"
    )
    commandline_parser.add_argument(
        "--email-to",
        metavar="<email address>",
        help="specify the email address to send email notifications to e.g. --email-to foo@bar.com",
    )
    commandline_parser.add_argument(
        "--email-username",
        metavar="<username>",
        help="specify the email account username e.g. --email-username foo@bar.com",
    )
    commandline_parser.add_argument(
        "--email-password", metavar="<password>", help="specify the email account password e.g. --email-password foo"
    )
    commandline_parser.add_argument(
        "--target-access-token",
        metavar="<token>",
        help="specify the github personal access token e.g. --target-access-token 123456789",
    )
    commandline_parser.add_argument(
        "--kodi-notification", action="store_true", help="enable kodi notification e.g. --kodi-notification"
    )
    commandline_parser.add_argument(
        "--email-notification", action="store_true", help="enable email notification e.g. --email-notification"
    )
    commandline_parser.add_argument(
        "--pidfile",
        metavar="<path>",
        help="specify path to pidfile e.g. --pid /var/run/triggerdockerbuild/triggerdockerbuild.pid",
    )
    commandline_parser.add_argument("--schedule", action="store_true", help="enable scheduling e.g. --schedule")
    commandline_parser.add_argument("--daemon", action="store_true", help="run as daemonized process e.g. --daemon")
    commandline_parser.add_argument("--version", action="version", version=version)

    # save arguments in dictionary
    args = vars(commandline_parser.parse_args())

    # set path to root folder for application
    app_root_dir = os.path.dirname(os.path.realpath(__file__))

    if not args["config"]:
        # set folder path for config files
        config_dir = os.path.join(app_root_dir, "configs")
        config_dir = os.path.normpath(config_dir)

    else:
        config_dir = args["config"]

    # set path for config.ini file
    config_ini = os.path.join(config_dir, "config.ini")

    # create config and logs paths if they dont exist
    if not os.path.exists(config_dir):
        os.makedirs(config_dir)

    # set path for configspec.ini file
    configspec_ini = os.path.join(app_root_dir, "configs/configspec.ini")

    # create configobj instance, set config.ini file, set encoding and set configspec.ini file
    config_obj = configobj.ConfigObj(
        config_ini,
        list_values=False,
        write_empty_values=True,
        encoding="UTF-8",
        default_encoding="UTF-8",
        configspec=configspec_ini,
        unrepr=True,
    )

    # create config.ini
    create_config()

    if not args["logs"]:
        # set folder path for log files
        logs_dir = os.path.join(app_root_dir, "logs")
        logs_dir = os.path.normpath(logs_dir)

    else:
        logs_dir = args["logs"]

    # set path for log file
    app_log_file = os.path.join(logs_dir, "app.log")

    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)

    # setup logging
    app_log = app_logging()
    app_logger_instance = app_log.get("logger")
    app_handler = app_log.get("handler")

    if args["email_notification"]:
        email_notification = args["email_notification"]

    elif config_obj["notification"]["email_notification"] is not None:
        email_notification = config_obj["notification"]["email_notification"]

    else:
        app_logger_instance.info(
            "Email Notification is not defined via '--email-notification' or 'config.ini', defaulting to False"
        )
        email_notification = False

    if not email_notification:
        app_logger_instance.info("Email notification is disabled")

    if args["email_to"]:
        email_to = args["email_to"]

    elif config_obj["notification"]["email_to"] is not None:
        email_to = config_obj["notification"]["email_to"]

    else:
        app_logger_instance.info(
            "Email To is not defined via '--email-to' or 'config.ini', setting Email Notification to false"
        )
        email_notification = False

    if args["email_username"]:
        email_username = args["email_username"]

    elif config_obj["notification"]["email_username"] is not None:
        email_username = config_obj["notification"]["email_username"]

    else:
        app_logger_instance.info(
            "Email Username is not defined via '--email-username' or 'config.ini', setting Email Notification to false"
        )
        email_notification = False

    if args["email_password"]:
        email_password = args["email_password"]

    elif config_obj["notification"]["email_password"] is not None:
        email_password = config_obj["notification"]["email_password"]

    else:
        app_logger_instance.info(
            "Email Password  is not defined via '--email-password' or 'config.ini', setting Email Notification to false"
        )
        email_notification = False

    if args["kodi_notification"]:
        kodi_notification = args["kodi_notification"]

    elif config_obj["notification"]["kodi_notification"] is not None:
        kodi_notification = config_obj["notification"]["kodi_notification"]

    else:
        app_logger_instance.info(
            "Kodi Notification is not defined via '--kodi-password' or 'config.ini', setting Kodi Notification to false"
        )
        kodi_notification = False

    if args["kodi_password"]:
        kodi_password = args["kodi_password"]

    elif config_obj["notification"]["kodi_password"] is not None:
        kodi_password = config_obj["notification"]["kodi_password"]

    else:
        app_logger_instance.info(
            "Kodi Notification is not defined via '--kodi-password' or 'config.ini', setting Kodi Notification to false"
        )
        kodi_notification = False

    if args["target_access_token"]:
        target_access_token = args["target_access_token"]

    elif config_obj["general"]["target_access_token"] is not None:
        target_access_token = config_obj["general"]["target_access_token"]

    else:
        app_logger_instance.warning(
            "Target Access Token is not defined via '--target-access-token' or 'config.ini', exiting script..."
        )
        exit(1)

    # Verify TLS certificates by default; allow opt-out for SSL-inspection environments.
    # The configspec defines boolean(default=True), so the key is always present.
    verify_ssl = config_obj["general"]["verify_ssl"]

    # Silence urllib3 InsecureRequestWarning only when verification is disabled
    _silence_tls_warnings(verify_ssl)

    # check os is not windows and then run main process as daemonized process
    if args["daemon"] is True and os.name != "nt":
        app_logger_instance.info("Running as a daemonized process...")

        # specify the logging handler as an exclusion to the daemon, to prevent its output being closed
        daemon_context = daemon.DaemonContext()
        daemon_context.files_preserve = [app_handler.stream]
        daemon_context.open()

    else:
        app_logger_instance.info("Running as a foreground process...")

    if args["schedule"] is True:
        app_logger_instance.info("Running via schedule...")
        scheduler_start()

    else:
        app_logger_instance.info("Running on demand...")
        ondemand_start()
