YSN - packaged for GitHub Actions build

Contents:
- ysn.py (application)
- ysn_downloader/ (helper package)
- .github/workflows/build.yml (workflow to build exe via PyInstaller on Windows runner)
- ysn-yt-dlp.iss, ysn-youtube-dl.iss (Inno Setup installer templates) - optional

To build an executable on GitHub:
1. Push this repository to GitHub.
2. The workflow at .github/workflows/build.yml will run on push to main and attempt to build with PyInstaller.
   - The workflow may require adjustments depending on your desired packaging (PyInstaller vs Inno Setup).
3. If you want to produce a Windows installer (.exe) via Inno Setup, you need to:
   - Ensure Inno Setup is available on the runner or add steps to install Inno Setup.
   - Provide aria2c.exe in thirdparty/aria2/ if you want it bundled; respect aria2 license.

Note: The included Inno Setup scripts are templates; adapt [Files] Source paths to point to your build outputs (dist\ysn.exe).
