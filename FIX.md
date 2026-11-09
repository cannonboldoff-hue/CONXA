# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**A run now sees what is happening in every tab and every part of the page — 2026-11-09**
Before, when a step started an upload, the app only looked at one tab and the main part of the page. Progress shown in a small embedded window, or a refusal that arrived in a second tab, could go unseen, so a run might say "Done" too early. Now the app keeps a short private notebook of what happens on every tab the workflow uses and in every visible part of the page, from just before the step until its result is clear. It also notices when a website says "OK" but its reply says the upload failed, for example because the storage is full. The notebook never keeps page contents, web addresses with personal details, or replies; it stays on your computer.
— 2026-11-09

**A run now says plainly when it could not be sure a step worked — 2026-11-09**
Before, a step that the app could not check was quietly counted as a success, so a run could say "Done" when nothing proved the work happened. Now every step is judged on how much was really shown. If a small step could not be confirmed, the run still finishes but says "Done, please check" and lists what to look at. If a step that cannot be undone, like an upload or a delete, could not be confirmed, the run stops and says "Couldn't confirm" instead of pretending, and it never repeats that step by itself. The test screen in the Build Studio shows these in everyday words, with a "What was checked" list one click down.
— 2026-11-09

**Uploading to Google Drive is no longer called finished while the upload is still running — 2026-11-09**
Drive shows a small upload window with Minimize, Close and Cancel all buttons. The app mistook it for a question and stopped waiting after a few thousandths of a second, so a run could say "Done" while the file was still on its way. It now knows that window is only a progress report and waits for it to finish. It also no longer treats a quiet page as finished until it has seen some sign the work started, stops waiting in time to tell you the upload was still running instead of going silent, and stops the run if a question is left open on the last step.
— 2026-11-09

**The plan for proving steps really worked is now in everyday language — 2026-11-09**
The design document was written for engineers, so it was hard for anyone else to follow. It now explains everything in plain words, using the example of sending a parcel. The technical details are still there, tucked behind sections that engineers can open.
— 2026-11-09

**A written plan for proving that every step really worked — 2026-11-09**
Clicking a button does not prove the work got done, and a file can look uploaded when it never arrived. A new design document explains how the app can check each action properly, what to do when it can't tell, and where those checks can still go wrong. While writing it we found that the new upload wait stops too early on Google Drive's real upload window, so that is listed to fix first. Nothing about how workflows run has changed yet.
— 2026-11-09

**A workflow is no longer called finished while its upload is still running — 2026-11-09**
A workflow that ended by uploading a file to Google Drive was reported as a success the moment the upload started, even if the upload was still in progress or later failed. Now, after any step that starts work, the app waits for the progress to clear and checks the page and the app's own messages. A final upload that fails, or is still not done after a few minutes, now stops the run with a plain message instead of saying it worked. This covers every workflow, not just Drive.
— 2026-11-09

**Testing now has a simple "Dry Run" switch — 2026-11-09**
The test screen used to offer "Let AI answer agent steps and unexpected popups", which was hard to understand. It is now called "Dry Run" and is on by default, which means a safe test that uses no AI credits. If the workflow has a step that needs AI, Dry Run turns itself off and stays off, so that step can actually run. Otherwise you can switch it off yourself to let the AI helper answer an unexpected popup.
— 2026-11-09

**The test browser now keeps the whole page in view after it opens a new website — 2026-11-09**
In a test, the second website could show up zoomed in, with its right side and bottom cut off, even though recording had shown the whole page. The "shrink to fit" setting was applied once when the tab opened, and it could be lost when the tab moved on to a real website. It is now applied again every time a page finishes loading, in both Build Studio and Conxa Execute. A test should now look the same as the recording.
— 2026-11-09

**Very long workflows keep their step-by-step descriptions — 2026-11-09**
When a workflow had a great many steps, the assistant that describes each step could run out of room mid-answer and hand back nothing. The workflow was still built, but the descriptions were silently missing. Now the assistant is given more room the longer the workflow is, and if its answer still gets cut off, the steps are described in smaller groups and put back together. If any step still cannot be described, you are told.
— 2026-11-09

**A guided first tour for beginners is now on the to-do list — 2026-11-09**
New people often open the app and do not know where to start. We wrote down a plan for a practice tour: you pick Beginner, and the app lights up one button at a time, with small notes in the browser telling you what to type. It uses a safe practice website, so nothing real can be broken, and you can pick the practice that matches your job. It is made for people with no technical background.
— 2026-11-09
