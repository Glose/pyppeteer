#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Chromium process launcher module."""

import asyncio
import asyncio.subprocess
import atexit
import json
from urllib.request import urlopen
from urllib.error import URLError
import logging
import os
import os.path
from pathlib import Path
import shutil
import tempfile
from typing import Any, Dict, TYPE_CHECKING

from pyppeteer.browser import Browser
from pyppeteer.connection import Connection
from pyppeteer.errors import BrowserError
from pyppeteer.util import check_chromium, chromium_excutable
from pyppeteer.util import download_chromium, merge_dict, get_free_port

if TYPE_CHECKING:
    from typing import Optional  # noqa: F401

logger = logging.getLogger(__name__)

pyppeteer_home = Path.home() / '.pyppeteer'
CHROME_PROFILIE_PATH = pyppeteer_home / '.dev_profile'

DEFAULT_ARGS = [
    '--disable-background-networking',
    '--disable-background-timer-throttling',
    '--disable-browser-side-navigation',
    '--disable-client-side-phishing-detection',
    '--disable-default-apps',
    '--disable-extensions',
    '--disable-hang-monitor',
    '--disable-popup-blocking',
    '--disable-prompt-on-repost',
    '--disable-sync',
    '--disable-translate',
    '--metrics-recording-only',
    '--no-first-run',
    '--safebrowsing-disable-auto-update',
]

AUTOMATION_ARGS = [
    '--enable-automation',
    '--password-store=basic',
    '--use-mock-keychain',
]


class Launcher(object):
    """Chrome parocess launcher class."""

    def __init__(self, options: Dict[str, Any] = None, **kwargs: Any) -> None:
        """Make new launcher."""
        self.options = merge_dict(options, kwargs)
        self.port = get_free_port()
        self.url = f'http://127.0.0.1:{self.port}'

        self.chrome_args = DEFAULT_ARGS
        self.chrome_args.append(
            f'--remote-debugging-port={self.port}',
        )
        self.chromeClosed = True
        if self.options.get('appMode', False):
            self.options['headless'] = False
        else:
            self.chrome_args.extend(AUTOMATION_ARGS)

        self._tmp_user_data_dir: Optional[str] = None
        self._parse_args()

        if self.options.get('devtools'):
            self.chrome_args.append('--auto-open-devtools-for-tabs')
            self.options['headless'] = False

        if 'headless' not in self.options or self.options.get('headless'):
            self.chrome_args = self.chrome_args + [
                '--headless',
                '--disable-gpu',
                '--hide-scrollbars',
                '--mute-audio',
            ]
        if 'executablePath' in self.options:
            self.exec = self.options['executablePath']
        else:
            if not check_chromium():
                download_chromium()
            self.exec = str(chromium_excutable())
        self.cmd = [self.exec] + self.chrome_args

    def _parse_args(self) -> None:
        if (not isinstance(self.options.get('args'), list) or
                not any(opt for opt in self.options['args']
                        if opt.startswith('--user-data-dir'))):
            if 'userDataDir' not in self.options:
                if not CHROME_PROFILIE_PATH.exists():
                    CHROME_PROFILIE_PATH.mkdir(parents=True)
                self._tmp_user_data_dir = tempfile.mkdtemp(
                    dir=str(CHROME_PROFILIE_PATH))
            self.chrome_args.append('--user-data-dir={}'.format(
                self.options.get('userDataDir', self._tmp_user_data_dir)))
        if isinstance(self.options.get('args'), list):
            self.chrome_args.extend(self.options['args'])

    def _cleanup_tmp_user_data_dir(self) -> None:
        if self._tmp_user_data_dir and os.path.exists(self._tmp_user_data_dir):
            shutil.rmtree(self._tmp_user_data_dir)

    async def launch(self) -> Browser:
        """Start chrome process and return `Browser` object."""
        env = self.options.get('env')
        self.chromeClosed = False
        self.connection: Optional[Connection] = None
        self.proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )

        def _close_process() -> None:
            if not self.chromeClosed:
                asyncio.get_event_loop().run_until_complete(self.killChrome())

        # dont forget to close browser process
        atexit.register(_close_process)

        connectionDelay = self.options.get('slowMo', 0)
        self.browserWSEndpoint = self._get_ws_endpoint()
        logger.info(f'Browser listening on: {self.browserWSEndpoint}')
        self.connection = Connection(self.browserWSEndpoint, connectionDelay)
        return await Browser.create(
            self.connection, self.options, self.killChrome)

    def _get_ws_endpoint(self) -> str:
        url = self.url + '/json/version'
        for i in range(100):
            await asyncio.sleep(0.1)
            try:
                with urlopen(url) as f:
                    data = json.loads(f.read().decode())
                break
            except URLError as e:
                continue
        else:
            # cannot connet to browser for 10 seconds
            raise BrowserError(f'Failed to connect to browser port: {url}')
        return data['webSocketDebuggerUrl']

    async def waitForChromeToClose(self) -> None:
        """Terminate chrome."""
        if not self.chromeClosed:
            self.chromeClosed = True
            self.proc.terminate()
            await self.proc.wait()
            self._cleanup_tmp_user_data_dir()

    async def killChrome(self) -> None:
        """Terminate chromium process."""
        logger.debug('terminate chrome process...')
        if self._tmp_user_data_dir and os.path.exists(self._tmp_user_data_dir):
            await self.waitForChromeToClose()
        else:
            if self.connection and self.connection._connected:
                await self.connection.send('Browser.close')


async def launch(options: dict = None, **kwargs: Any) -> Browser:
    """Start chrome process and return :class:`~pyppeteer.browser.Browser`.

    This function is a shotcut to :meth:`Launcher(options, **kwargs).launch`.

    Available options are:

    * ``ignoreHTTPSErrors`` (bool): Whether to ignore HTTPS errors. Defaults to
      ``False``.
    * ``headless`` (bool): Whether to run browser in headless mode. Defaults to
      ``True`` unless ``appMode`` or ``devtools`` options is ``True``.
    * ``executablePath`` (str): Path to a Chromium or Chrome executable to run
      instead of default bundled Chromium.
    * ``slowMo`` (int|float): Sles down pyppeteer operations by the specified
      amount of milliseconds.
    * ``args`` (List[str]): Additional arguments (flags) to pass to the browser
      process.
    * ``ignoreDefaultArgs`` (bool): [not implemented yet] Do not use
      pyppeteer's default args. This is dangerous option; use with care.
    * ``userDataDir`` (str): Path to a user data directory.
    * ``devtools`` (bool): Whether to auto-open a DevTools panel for each tab.
      If this option is ``True``, the ``headless`` option will be set
      ``False``.
    * ``appMode`` (bool): Deprecated.

    .. note::
        Pyppeteer can also be used to control the Chrome browser, but it works
        best with the version of Chromium it is bundled with. There is no
        guarantee it will work with any other version. Use ``executablePath``
        option with extreme caution.
    """
    return await Launcher(options, **kwargs).launch()


async def connect(options: dict = None, **kwargs: Any) -> Browser:
    """Connect to the existing chrome.

    ``browserWSEndpoint`` option is necessary to connect to the chrome. The
    format is ``ws://${host}:${port}/devtools/browser/<id>``. This value can
    get by :attr:`~pyppeteer.browser.Browser.wsEndpoint`.

    Available options are:

    * ``browserWSEndpoint`` (str): A browser websocket endpoint to connect to.
      (**required**)
    * ``ignoreHTTPSErrors`` (bool): Whether to ignore HTTPS errors. Defaults to
      ``False``.
    * ``slowMo`` (int|float): Slow down pyppeteer's by the specified amount of
      milliseconds.
    """
    options = merge_dict(options, kwargs)
    browserWSEndpoint = options.get('browserWSEndpoint')
    if not browserWSEndpoint:
        raise BrowserError('Need `browserWSEndpoint` option.')
    connection = Connection(browserWSEndpoint)
    return await Browser.create(
        connection, options, lambda: connection.send('Browser.close'))


def executablePath() -> str:
    """Get executable path of default chrome."""
    return str(chromium_excutable())
