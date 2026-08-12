"""Launch wrapper — disables Windows system proxy before gradio/httpx import.

Gradio's launch() calls httpx.get() for analytics/version check, and httpx
reads proxy settings from the Windows registry via urllib.request.getproxies().
This wrapper ensures NO_PROXY=* is set BEFORE any libraries are imported.
"""
import os
import sys

# 1. Force-disable ALL proxies via env vars (httpx checks these at call time)
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['ALL_PROXY'] = ''
os.environ['all_proxy'] = ''
os.environ['NO_PROXY'] = '*'  # KEY: httpx.get_environment_proxies() checks this
os.environ['no_proxy'] = '*'

# 2. Disable Gradio telemetry and HuggingFace hub calls
os.environ['GRADIO_ANALYTICS_ENABLED'] = 'False'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

# 3. Force UTF-8 for console output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 4. Patch urllib.request.getproxies (affects libraries imported AFTER this)
import urllib.request
urllib.request.getproxies = lambda: {}

# 5. Force a specific port (avoids conflicts with stale processes)
os.environ['GRADIO_SERVER_PORT'] = ''  # app.py handles the port

# 6. Run the app
import runpy
sys.argv = ['app.py']
runpy.run_path('app.py', run_name='__main__')
