# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**A test no longer breaks when you minimize the app or open something over it — 2026-11-14**
The test window is only drawn while you can see it. When Studio was minimized, covered or had a dialog open, a test could end with "the test browser is not showing the page at its normal size", and a failed test could hang for half a minute before showing its picture. Now the test page keeps its size and its place whether or not it is on screen, a new tab is given its size straight away, and Conxa takes the failure picture through the app itself instead of waiting for a window that is not drawing.
— 2026-11-14

**The workflow summary now shows how each step is checked — 2026-11-14**
Before, the summary listed only the actions, so you could not see how Conxa would know each one worked. Now every step has small "Check that…" lines under it, numbered like 4a and 4b. Anyone can read the checks without opening the editor, and the total count appears at the bottom.
— 2026-11-14

**A Google Drive upload no longer fails on a harmless "Maximized" notice — 2026-11-14**
A test run stopped at the Drive upload step saying the app reported "Maximized", even though that is only a notice Drive reads out when its upload panel opens. Conxa had wrongly learned that word as an error word while watching the recording. It now learns error words only from steps that did not finish successfully. Rebuild the workflow once so the old wrong word is dropped.
— 2026-11-14

**A test that really worked no longer ends as "needs a decision" or "still working" — 2026-11-14**
A test uploaded a file to Google Drive successfully every time, but Conxa ended two runs asking you to decide about a popup that was only the little preview card Drive shows when the mouse rests on a file, and parked the third run forever because a finished upload's progress bar never left the screen. Conxa now looks again after moving the pointer away before it stops on a popup, stops waiting for a progress bar that has not moved, and no longer counts a word that was already on the page as proof that a step worked. The test browser also keeps your mouse and keys off the page while a test runs, so a hover can't spoil it.
This means a working workflow is no longer reported as stuck, and a step is only called done when something actually changed because of it.
— 2026-11-14

**The browser now lets go for the person's turn, and keeps your hands off the rest of the time — 2026-11-14**
While a workflow runs in the app's browser, a stray click or a mouse resting on the page can change what the page shows and throw the run off. The test browser already ignored your clicks; now the Run window in Conxa Execute does too. When a step is meant for you, like entering a code or solving a check, the browser now opens up so you can click, then closes again once you press Done.
This means a run can't be spoiled by an accidental click, and you are never locked out of a step that needs you.
— 2026-11-14

**The helper that suggests what proves a step worked now talks to the right address — 2026-11-14**
Studio can ask an outside helper which kinds of proof fit each step. We had guessed the wrong address and the wrong way of asking, so it could never have answered. It now uses the address and format the provider documents, and it reuses the same key as the other models.
This means the next workflow you record can actually get those suggestions.
— 2026-11-14

**The earlier fixes now work for any website, not just the one that showed the problems — 2026-11-14**
Some of the first fixes only treated what we saw on one recording. They now follow general rules. When an app shows an upload or a save, Studio only trusts the "finished" message that appears in the same place as the progress, so a window notice or a tooltip can't pass for proof. It no longer builds a check out of words you just clicked or typed, since those are only true for your recording. The upload step is found by what it is (a file chooser), not by where it sits on the page. Only things you actually typed or picked are asked about as "typed". A workflow's sign-in status now follows your current sign-ins instead of a stale copy, and the recording message counts what you did, not what the page did by itself.
— 2026-11-14

**A broken build step is now shown, and a test that can't prove the upload no longer says "passed" — 2026-11-14**
When a recording was built, one helper step could crash without telling anyone. That left some steps with no description, refused your answer to "How can we tell it worked?", and still ended with a plain "passed". Now a crashed build step shows a clear "Part of the build failed" message with a Build again button, and the reason is one click away. The test card says "Couldn't confirm it finished" when nothing proved the work was done, and Approve only opens after you press "I checked — it worked".
Studio also no longer mixes up menu entries (it called "File upload" by the name of the entry above it), no longer treats on-screen clutter like "maximized" as proof an upload worked, no longer asks about "things you typed" that were really page changes, and drops old checks that only fit the file you recorded with.
— 2026-11-14

**Studio can now add smarter proof that a step really worked — 2026-11-14**
After a step that sends, saves or downloads something, Studio has to know the work really happened, not just that a button was pressed. Until now fixed rules chose how to check that. Studio can now ask a small, fast decision helper which kinds of proof fit each step, and it can add four new kinds: the page moved on and stayed there, what you entered shows up again on the page, what you entered is still there when the page is opened again, and a later step that needs the result worked. Each new check is only added when your recording really contains what it needs, none of them can stop a run on its own, and if the helper is off or unreachable everything works as before. These have been tested in isolation but not yet on a real website.
— 2026-11-14

**The test browser now fills three quarters of the window with nothing beside it — 2026-11-14**
While the test browser was open, a thin strip of two small icons sat on its right edge and took space from the browser. That strip is now hidden while the browser is open, and the browser always takes 75% of the window. The strip comes back as soon as you close or hide the browser.
— 2026-11-14

**A first hands-off test run of the Build Studio, and what it found — 2026-11-15**
Instead of a person clicking through the app, a script drove the Build Studio and its recording browser to record, build and test three small workflows. It showed the whole trip works, but found that a second new workflow in the same group can't be tested the first time (the test says it can't find the workflow). A full written report is in the testing folder.
— 2026-11-15

**More hands-off testing: real websites, the pop-up pages and the agent route — 2026-11-15**
The same script-driven testing was extended to real websites and to the pages that test pop-ups and moved buttons. Simple clicks, a download and a pop-up alert replayed cleanly, but a button whose name changes could not be recovered, because Conxa treats its label like a row in a list and then refuses to guess. The report explains each result and what to look at next.
— 2026-11-15

**Running finished workflows the way a customer's AI assistant would — 2026-11-15**
Instead of the Build Studio's test button, finished workflows were run directly through the runtime, several at once, stopped halfway and repeated. They ran side by side, stopping one left the other alone, and eight repeats took the same time each. When the runtime could not find the right button it asked the assistant to choose, and it refused a choice that was not clear enough, which is the safe behavior.
— 2026-11-15

**Testing that a "Remove" click never lands on the wrong row — 2026-11-15**
A small cart page was used to check that Conxa removes the item it was shown, not just whatever sits in the same position. When the same item appeared twice, Conxa refused to guess, which is right. But the Studio got stuck before that test could run, showing an unfriendly internal message, and left an unfinished step behind.
— 2026-11-15

**Checking Conxa works with the accounts you signed in, plus decisions made in the middle of a workflow — 2026-11-15**
Conxa reused the saved GitHub and Google sign-ins to record and replay two harmless read-only tasks. It also removed the right cart item even when the rows were shuffled, refused when the item was missing or listed twice, and paused for a person on a hand-over step and carried on once they pressed Done. Two rough edges remain: a few Studio messages still use internal words, and the code-checking tool for the Studio reports false errors.
— 2026-11-15

**Downloading twenty files in a row, and switching plans — 2026-11-15**
Conxa turned twenty file downloads into a repeating loop on its own, but the replay managed nine files before stopping with a clear error. While checking plan limits, a test accidentally switched the local test workspace to the Pro plan for a few minutes. That was put back to its original setting straight away.
— 2026-11-15

**A longer run of repeated workflows, and which features each plan unlocks — 2026-11-15**
One workflow was run 25 times in a row with no restarts, and every run worked at the same speed with no extra memory used. Plans were also switched on a test copy of the cloud: the analytics page is closed on the Free plan and open from Starter up. This time everything was saved first and put back exactly afterwards.
— 2026-11-15

**Trying a shopping-site test, and a speed check of the cloud — 2026-11-15**
A shopping demo site was signed in successfully, but the Studio could not confirm the sign-in and kept asking to sign in again, so no recording could start. A speed test showed the cloud answers most requests in under a tenth of a second, though two pages take about half a second each when many people ask at once.
— 2026-11-15

**Dragging items and the limits of each plan, checked again — 2026-11-15**
A drag-and-drop was recorded for the first time, but the replay said it passed without the drop actually happening, so that result cannot be trusted yet. The checks that protect the paid-plan limits (machines, seats, trial end, credits) all passed in the project's own tests. The shopping-site sign-in still could not be completed.
— 2026-11-15

**Publishing a test skill to the local cloud, and a drag replay that does nothing — 2026-11-15**
A finished test skill was published to the local test cloud and appeared in its version list with the right note. A recorded drag-and-drop turned out to replay as "done" without dragging anything, because the saved step never says what to pick up. Both are written up in the test report.
— 2026-11-15

**Why the "Github to Drive Handoff" test keeps failing — 2026-11-15**
The workflow was run once with the signed-in accounts. It stopped because one step opens a misspelled web address ("dive" instead of "drive"), so nothing was uploaded and nothing in Google Drive changed. Correcting that one address should fix the workflow.
— 2026-11-15

**The Build Studio's own automatic checks — 2026-11-15**
All of the Build Studio's built-in checks were run. Everything passed except one layout check about how a small window is divided, probably because the test browser was recently changed to take three quarters of the window and the check was never updated.
— 2026-11-15

**A long recording on a real signed-in website — 2026-11-15**
A forty-click tour of a public code project, recorded while already signed in, turned into a 41-step workflow with 24 checks. The replay then stopped at the third click, because a link's name included a number that changes (for example the count of open requests). Links whose names contain live numbers are fragile and need cleaning up.
— 2026-11-15

**Checking that recompiling a recording gives the same result — 2026-11-15**
The same recording was turned into a workflow twice, and every step was found by exactly the same clues both times. The older helper script for this check no longer works on its own and needs updating.
— 2026-11-15

**Checking the cloud refuses to run half-configured — 2026-11-15**
When the cloud was started in its protected mode without a database, sign-in provider and telemetry key, it refused to start and listed exactly what was missing. That is the safe behavior. Whether each page then rejects an unsigned request could not be checked without a full production setup.
— 2026-11-15

**Running the customer's program directly — 2026-11-15**
The single program that customers run was started on its own. It was ready in about a second, showed separate version numbers for the program and the part that updates itself, listed all 42 workflows and ran one in nine seconds. Updating a real installed copy was not tested.
— 2026-11-15

**Checking the customer program refuses an update it cannot run — 2026-11-15**
A copy of the customer program was given an updatable part that claimed to need a much newer version of the program. It refused to start with a plain message to reinstall, which is the safe behavior. Downloading a real update was not tested.
— 2026-11-15

**The long website tour now runs from start to finish — 2026-11-15**
The forty-click tour of a public code project failed because its links were saved by name, and the names contained numbers that change. After pointing each link at its stable web address, all 41 steps ran through in about 19 minutes. That is slow, and it is longer than the Studio's own test button allows, so a workflow this long cannot pass that button.
— 2026-11-15

**The same workflow on a different language of Wikipedia — 2026-11-15**
A search-and-open-the-first-result workflow was recorded on English Wikipedia and then pointed at German Wikipedia. On the English page it refused to guess between two close matches, which is safe. On the German page it opened the right article in 17 seconds and each step was confirmed.
— 2026-11-15

**One workflow that visits two different websites — 2026-11-15**
A search on a shopping practice site followed by a search on Wikipedia was recorded as a single workflow. The replay did the first site's steps, moved on to Wikipedia as it should, and then stopped at the last click because several results looked equally good. So the hop between sites works; the last click needs a clearer target.
— 2026-11-15

**A three-page practice run, and a privacy check that stopped a build — 2026-11-15**
A longer recording across several practice pages turned into a ten-step workflow with checks. An earlier version of it was refused at the build stage because a typed email address had ended up in how the workflow finds a button, which is exactly what the privacy check is for. The final replay did not complete because the test script clicked the wrong link.
— 2026-11-15

**A correction to the three-page practice run — 2026-11-15**
The earlier explanation for why the three-page replay failed was wrong. The click was right, but the workflow wrongly expected the page address to change after it, so the run kept retrying and failed after about nine minutes. That wrong expectation probably came from how the test was recorded. A wrong expectation should fail fast, not slowly.
— 2026-11-15

**The three-page practice run, done properly — 2026-11-15**
When the test script paused briefly after each click, the three-page, two-website workflow ran all nine steps in 17 seconds. That confirms the earlier failure came from how the recording was made, not from Conxa being unable to run it.
— 2026-11-15

**Why the shop-site sign-in kept looping, and what it showed next — 2026-11-15**
The Studio only learns that a sign-in worked by comparing the sign-in page when signed in and signed out. On a site whose sign-in page looks the same either way, it learned nothing and asked again forever. Pointing the app at a page that needs sign-in fixed it. The shopping test then recorded fine, but the replay stopped when adding the third item, refusing to choose between rows that were not actually ambiguous.
— 2026-11-15

**A busy HR demo app, signed in with the new sign-in setting — 2026-11-15**
The same sign-in fix worked on a second site: the HR demo app was accepted as signed in on the first try. The recorded steps got into the Admin area, but the replay then stopped because the search box had no clear name and appeared late. The test card also pointed at the wrong step.
— 2026-11-15

**A correction about the shopping-site test — 2026-11-15**
The shopping test stopped when adding the third item because two different products on that site cost the same price, and the workflow had identified each item by its price instead of its name. The refusal to guess was right; the weak choice of price was the problem.
— 2026-11-15

**The banking practice site could not be signed in — 2026-11-15**
The sign-in fix that worked on two other sites did not work on the banking practice site, because when signed out that site shows an error page at the same address instead of sending you to a login page. The Studio could not tell the difference, so the long banking test could not be started.
— 2026-11-15

**Creating a test account on a public demo site was stopped — 2026-11-15**
The last review-writing test needed a brand new account on a public practice shop. Setting it up was stopped at the permission check, because creating accounts on a site that is not yours should only happen when you say so. Nothing was submitted.
— 2026-11-15

**A longer repeat run found a stuck run that never ended — 2026-11-15**
One workflow was run again and again in the same program. The first thirty runs took the same time and used the same memory, then one run stopped at its very first step and never finished or failed, and the program stayed stuck for about sixteen minutes. The built-in time limit that should end such a run did not step in. A fresh copy of the program ran the same workflow normally.
— 2026-11-15

**The stuck run happens every time at the thirty-first run — 2026-11-15**
The same repeat test was run again on a fresh copy of the program, and the program got stuck at exactly the same point, the thirty-first run, with no error and no timeout. The cloud and the website were fine the whole time, so the fault is inside the long-running program itself.
— 2026-11-15

**The stuck run is not caused by the cloud connection — 2026-11-15**
With the connection to the cloud switched off, the repeat test ran faster, about three and a half seconds per run instead of nine. It still got stuck at exactly the thirty-first run, so the cloud was not the cause. The cause is somewhere inside the long-running program and has not been found yet.
— 2026-11-15

**Looking inside the stuck program — 2026-11-15**
The repeat test got stuck at the thirty-first run a fourth time. This time the built-in inspector could not get any answer from the stuck program, which means it is frozen in the operating system rather than just waiting. The cause is still not found, and a simple restart after about twenty-five runs would hide it for customers.
— 2026-11-15

**Checking that a bad update rolls back by itself — 2026-11-15**
A deliberately broken update was placed next to a working one. The customer program noticed it could not load the broken one, switched back to the working one on its own and started normally. One problem came up on the way: in the developer setup the updates are stored under a name the rollback does not recognise, so it would not work there. It is not yet known whether real customer installs use the same name.
— 2026-11-15

**Writing a review on the Juice Shop demo, and a mail workflow that cannot be built — 2026-11-15**
With your go-ahead a practice account was created on the Juice Shop demo and a product review was really posted, but the replay stopped because several Apple products matched. A mail workflow in Gmail could not be built at all, because the privacy check sees the signed-in address in the page title of every Gmail screen and refuses. No email was sent by a replay.
— 2026-11-15

**What the mail tests really did in the mailbox — 2026-11-15**
No email was sent by any test. But the failed mail replays left seven half-written drafts in the Drafts folder of the account used. They also showed that a mail workflow would have been sent with no subject, because the recorded subject typing was left out when the workflow was built.
— 2026-11-15

**The Juice Shop review now works end to end — 2026-11-15**
The workflow that writes a product review was refused earlier because the search matched two products, "Apple Juice" and "Pineapple Juice". After pointing the click at the exact product, the run wrote the review, and the shop's own listing confirmed it. The Studio still said it could not confirm the review, which is cautious.
— 2026-11-15

**Why a mail workflow still cannot type its message — 2026-11-15**
After adding back the missing subject and fixing the message box target, the mail workflow typed the recipient and subject, then stopped when typing the message. The Studio checks typing by reading a box's value, and a mail message box has no value, only text inside it, so the check fails even though the text was typed.
— 2026-11-15

**Two more ways to sign in to the banking practice site, both refused — 2026-11-15**
Every signed-in page on the banking practice site shows an error page at the same address when you are signed out, and its sign-in page looks the same either way. So the Studio had nothing to compare in either case and would not accept the sign-in. The long banking test therefore cannot be started with the Studio's own check.
— 2026-11-15

**Checking the billing and sign-in safeguards through the project's own tests — 2026-11-15**
The payment-provider checks (a message with a bad signature is refused, and a failed update never answers "OK") and the sign-in and team checks all passed in the project's own automatic tests, 13 and 117 of them. A real sign-in or a real payment was not tried, since that needs real accounts.
— 2026-11-15

**A customer-style first sync skipped itself, and the saved sync address looks wrong — 2026-11-15**
The customer program's install-time sync ran and then politely skipped itself because the saved pack file had no sync key. The address stored in that file for fetching skills also does not exist on the local cloud, while the address the publish step reported does. So a real sync from this saved file would likely fail even with a key.
— 2026-11-15

**A mail sent from a pre-filled link — 2026-11-15**
Opening the mail service from a link that already holds the recipient, subject and message, then clicking Send, worked: one test message was sent with its subject and text. The recording stopped by itself because that window closes on send, so no repeatable workflow came out of it.
— 2026-11-15

**A mail workflow with per-row values, and Google saying stop — 2026-11-15**
A mail workflow was built that opens a pre-filled message from a link carrying the recipient, subject and text for each row. The Studio filled those three values in by itself and the test runs found the Send button. The real sends for the last two rows were then refused by Google with "no access" and "too many requests", so they were stopped to avoid locking the account.
— 2026-11-15

**Building a "do this for every row" loop by hand, and a safety that depends on step names — 2026-11-15**
A repeat-for-each-row step was added to the mail workflow and it built, but the list of rows could not be supplied, so the values in the message link stayed as raw placeholders and nothing was sent. A hand-added click step had no name, and the "test without sending" mode decides what to hold back from the step's name, so that click was not held back.
— 2026-11-15
