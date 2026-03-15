- Check if fill has a flag to disable log emission entirely (still writes under
  `logs/` even after removing `--log-to`). If available, test impact on runtime.
- Explore safe use of `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` with explicit plugin
  loading to trim startup overhead, if it doesn’t break fill’s plugins.
