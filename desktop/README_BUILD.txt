Secret Sauce — Desktop (local) edition
======================================

What this is
------------
A local, double-click version of Secret Sauce for Windows. The tech picks a
folder of OTDR files (.sor / .trc / .json) on their own machine and gets a
report written next to the files. Nothing uploads, nothing goes to the cloud,
and there is NO file-size limit — it uses the machine's own memory, so the big
runs that crashed the website (Platteville-size, 1000+ fibers) work fine here.

Folder contents
---------------
  desktop_app.py            the local Streamlit app (folder picker, runs engine)
  launcher.py               PyInstaller entry point (boots Streamlit, opens browser)
  report.py / report_sor.py the analysis engine (snapshot of the cloud version)
  sor_reader324802a.py
  trc_parser.py
  zerodblogo.png
  SecretSauce.spec          PyInstaller build recipe
  build.bat                 one-click Windows build script
  requirements-desktop.txt  build dependencies
  README_BUILD.txt          this file


HOW TO TRY IT WITHOUT PACKAGING (any machine with Python)
---------------------------------------------------------
  pip install streamlit numpy scipy matplotlib openpyxl
  streamlit run desktop_app.py
A browser opens; click Browse, pick a folder, Run.


VALIDATION STATUS
-----------------
This recipe was test-built and LAUNCH-TESTED on a Mac (throwaway build) to
de-risk the Windows build. Two real packaging bugs were found and fixed here
so they won't bite the Windows build:
  * pkg_resources needed 'appdirs' (+ jaraco/more_itertools) -> added to the
    spec's hiddenimports.
  * Newer setuptools crashed the packaged app at launch with InvalidVersion
    -> pinned setuptools==65.5.1 in requirements-desktop.txt.
  * Streamlit's first-run email prompt would hang a double-click app -> the
    launcher now pre-seeds credentials + forces headless.
After these, the packaged binary built and served the app cleanly. The
Windows build uses the same spec + pinned requirements, so it should go green
on the first try.


HOW TO BUILD THE WINDOWS .EXE
-----------------------------
Do this ON a Windows machine (PyInstaller cannot cross-compile from Mac).

  1. Install Python 3.11 (64-bit) from python.org — use 3.11, NOT 3.12. We pin
     setuptools 65.5.1, whose pkg_resources uses pkgutil.ImpImporter, which 3.12
     removed (the packaged app would die at launch). During install, tick "Add
     Python to PATH".
  2. Copy this whole SecretSauce-Desktop folder onto the Windows machine.
  3. Open the folder, double-click  build.bat  (or run it from a terminal).
     It creates a clean environment, installs everything, and builds the app.
     Takes several minutes the first time.
  4. When it finishes, the app is at:
         dist\SecretSauce\SecretSauce.exe
  5. Zip the  dist\SecretSauce  folder. That zip is what you give to a tech.


HOW A TECH USES IT
------------------
  1. Extract the zip anywhere (Desktop is fine).
  2. Double-click  SecretSauce.exe  inside the extracted folder.
     - No window appears at first (it runs quietly in the background).
     - After a few seconds a browser tab opens to the app.
  3. Click "Browse…", pick the folder of fiber files, choose Excel, Run.
  4. The report appears in a  SecretSauce_reports  subfolder next to the files,
     and can also be downloaded from the page.
  5. To quit: click the "Quit Secret Sauce" button in the left sidebar (it
     stops the background app), then close the browser tab.
     (If they forget, the background app just keeps running harmlessly on
     localhost until the next reboot, or it can be ended in Task Manager.)
  Logs, if ever needed for troubleshooting, are written to:
     C:\Users\<name>\.secretsauce\secretsauce.log


FIRST-RUN STEPS FOR A TECH (canonical sequence — keep these identical
everywhere you publish the link):
  1. Download the zip.
  2. Right-click the zip -> Properties -> tick "Unblock" -> OK.
  3. Right-click the zip -> Extract All.
  4. Open the SecretSauce folder -> double-click SecretSauce.exe.
  5. SmartScreen "Windows protected your PC" -> More info -> Run anyway.

NOTES / GOTCHAS
---------------
  * First launch is slow (10-30s) and shows NO window while it unpacks/boots —
    that's normal, not a freeze. A browser tab opens on its own when ready.
  * Windows SmartScreen warns once for an unsigned app (step 5 above). To
    remove it entirely you need a code-signing certificate (separate purchase).
  * Output is Excel by default. PDF works if Chrome OR Microsoft Edge is
    installed — Edge ships with Windows 10/11, so PDF works on virtually any
    machine. (The build excludes the heavyweight PDF library and renders via a
    Chromium browser instead.)
  * AUTO-UPDATE: on launch the app pulls the latest report.py / report_sor.py /
    sor_reader / trc_parser / desktop_app.py from the repo's main branch,
    validates them (compile + import smoke check), and runs those — so engine
    changes go live on every machine on the next launch with no re-download.
    The bundled copies here are the OFFLINE fallback. Only changes to the
    bundled dependencies or to launcher.py itself require a fresh download +
    rebuild. The sidebar shows whether the running code is "latest" or "bundled".
