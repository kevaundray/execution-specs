- Check if fill has a flag to disable log emission entirely (still writes under
  `logs/` even after removing `--log-to`). If available, test impact on runtime.
- Try modest worker caps (14–18) or leaving cap unset on hardware with more
  cores to see if a slight concurrency tweak beats the current 16 cap.
- Consider reducing stdout volume (e.g., `-q`) if the fill CLI supports it, to
  cut console I/O overhead from the massive test list.
- Explore safe use of `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` with explicit plugin
  loading to trim startup overhead, if it doesn’t break fill’s plugins.
