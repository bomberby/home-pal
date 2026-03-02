import builtins
import logging
import re
import warnings
from datetime import datetime


class _MuteRoutes(logging.Filter):
    _PATTERN = re.compile(r'"[A-Z]+ (?:/health|/sh/led|/persona/status|/cam/) HTTP/')

    def filter(self, record):
        return not self._PATTERN.search(record.getMessage())


class _NoSafetyCheckerWarning(logging.Filter):
    def filter(self, record):
        return 'safety_checker' not in record.getMessage()


def _install_timestamped_print():
    _real_print = builtins.print

    def _tprint(*args, **kwargs):
        ts = datetime.now().strftime('%H:%M:%S')
        _real_print(f'[{ts}]', *args, **kwargs)

    builtins.print = _tprint


def configure():
    _install_timestamped_print()
    logging.getLogger('werkzeug').addFilter(_MuteRoutes())
    logging.getLogger('diffusers.pipelines.stable_diffusion.pipeline_stable_diffusion').addFilter(_NoSafetyCheckerWarning())
    warnings.filterwarnings('ignore', message='.*torch_dtype.*')
    warnings.filterwarnings('ignore', message='.*safety_checker.*')
