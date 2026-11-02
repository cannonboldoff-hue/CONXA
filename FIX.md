# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Stopped saved sign-ins from wrongly showing as expired — 2026-11-02**
Studio learns what "signed in" looks like by comparing a signed-in page with a signed-out one. It only looked at the first handful of links, so on busy pages the small links at the bottom (like "Docs" or "Contact Support") were missed and mistaken for signs of being signed out. That made a perfectly good sign-in flip to "expired". Now the bottom of the page is ignored and many more links are checked. Like judging whether a shop is open by its front door, not the notices in the back corridor.

**Showed a download or new tab under the click that caused it — 2026-11-02**
A click that starts a download used to look like two unrelated rows in the step list, which read like a mistake. Now the second row is tucked in just under the click with a small arrow, in both the chat summary and the editor. They are still two separate steps that run and edit on their own, so a small fix touches only the part you meant. Like a receipt that indents the tax line under the item it belongs to.

**Fixed groups never reaching the cloud, and made a failed recording read-back explain itself — 2026-11-02**
Every time the group list opened, Build Studio tried to save each group on the cloud and got "not found", because it was knocking on a door the cloud had renamed months ago. It now uses the right address, so groups show up on the cloud's Skill Packages page before the first publish. Separately, when the step that reads a recording back fails, Studio now keeps a short note of what came back, like a doctor writing down the symptom instead of just "unwell", and it gets more room to write its answer in case the reply was being cut off.

**Stopped tests from pausing on pop-ups that are not questions — 2026-11-02**
A test used to halt and ask for a decision whenever anything unfamiliar showed up, even a harmless "Uploading 1 item" message with nothing to choose. Now a workflow carries on as recorded and only stops to ask a person where someone asked for that in the chat, and a risky choice nobody approved ends the run with a clear note instead of a guess. The questions before building now also ask about an item picked from a list, a value you turn into an input is updated everywhere it was copied, and ending a test at a pop-up is reported as exactly that. Like a car that no longer slams the brakes for a shadow on the road.

**Made test browsers reuse what they already downloaded — 2026-10-30**
Every test used to open a brand-new browser that had never seen any website, so it re-downloaded every page and picture from scratch. On a shaky connection that made tests crawl. Now tests keep a shared stash of downloaded site files between runs, while still starting each test signed out. Like a library that keeps the books you borrowed, but forgets who you are.

**Added a pencil to go back to any earlier question — 2026-10-30**
In a long chat there was no way to change an answer you had already given. Now every answered question has a small pencil; press it, confirm, and the workflow returns to how it was just before that answer, and you choose again. Anything you did after that point is discarded, and questions asked before the build also rebuild the workflow from your new answer. It works like rewinding a video to a chapter marker and playing on from there.

**Made "Test it" work after choosing "Test changes" in the editor — 2026-10-30**
Choosing "Test changes" in the editor already put a test setup card in the chat, so pressing "Test it" there found one waiting and did nothing visible. Now pressing it folds the old card away and puts a fresh one at the bottom of the chat, with the latest values. It is like a waiter clearing the old menu and handing you a new one instead of pointing at the one behind you.

**Added an on/off switch for AI in test runs — 2026-10-30**
Before, a test always let the AI answer any "AI review" step on its own, using up credits without asking. Now the test setup screen has a "Let AI answer review steps" switch, off by default. While it is off, the test stops at the first such step instead of spending credits; switch it on when you want the AI to answer, like engaging the autopilot only when you choose to.

**Questions now read as one clear heading — 2026-10-30**
Each question used to open with the question itself and bury the explanation underneath, then a faint grey line and a "Question 2 of 2" label crowded the top. Now the short explanation and the question sit together as one bold heading, with the choices right below and the small back and forward arrows at the bottom. The question counter is gone.

**The workflow summary now comes back after you apply a change from chat — 2026-10-30**
After accepting a suggested change, the chat just showed "Applied" and the workflow overview card stayed far up the page, out of date. Now a fresh overview card appears right below, showing the new version number, step list and the Test it button. Like a receipt printed after every edit, so you always see where the workflow stands.

**Removed the "Show the browser while it runs" tick box from the test screen — 2026-10-30**
The test screen asked you to tick a box before you could watch the browser during a test. That choice is gone, so the screen is shorter and there is one less thing to think about. The test still runs the same way it did with the box left at its default.

**The Test it button now disappears while the chat is still answering your change request — 2026-10-30**
After you asked for a change, the Test it buttons stayed on screen while the reply was being written, so it was easy to test the old version by mistake. Now they hide the moment you send your message and come back once the reply, and any proposed change, has arrived. Like a "do not enter" sign on a room while the cleaners are still working inside.

**Curly quotes in chat messages no longer turn into strange symbols — 2026-10-30**
A message like Download “github file” file showed up in the chat as "â€œgithub fileâ€�". The app was reading what you typed with the wrong alphabet, like reading a French letter with a German dictionary. It now reads it correctly, so your words appear exactly as typed. Messages already saved with the symbols stay as they are; send them again.

**Stopped a garbled-character error when sending typed text — 2026-10-30**
Typing certain characters (like curly quotes or emoji) into the app could trigger a red "can't encode character" error and the text was lost. Now the app repairs or replaces any broken character before saving it, like a spell-checker catching a smudged letter before the page is filed. Typed answers and chat messages now save normally.

**Added a "type your own answer" box to every question — 2026-10-30**
Before, a question only offered its fixed choices, so you had no way to say "none of these, I want something else". Now each question has a small box under the choices where you can write your own answer and press Enter. Picking a choice moves straight to the next question, and small back and forward arrows replace the old buttons. Once the workflow is built, your own words go to the chat, which suggests a change you can accept or reject; before it is built, your words are saved as a note.

**Stopped the "finish or close the login window" error from blocking you — 2026-10-30**
Starting a sign-in for one app while another sign-in window was still open showed an error and made you close the old window by hand. Now the unfinished sign-in is closed for you and the new one opens, like swapping one phone call for another. A recording in progress is still protected and is never closed this way.

**Stopped a failed recording from wiping a saved sign-in — 2026-10-30**
When a recording opened and the app showed its login page instead, then was discarded, Conxa saved that signed-out browser over the good saved sign-in. The next check then said the sign-in had expired, even though it was fine a minute earlier. Now a discarded recording never touches saved sign-ins, a finished one only updates them if it is still signed in, and a backup of the old one is kept. If a recording opens on a login page, Conxa now stops it straight away and asks you to sign in, instead of leaving you on a blank recording.

**Stopped signing-in from being recorded as part of a workflow — 2026-10-30**
If a saved login ran out in the middle of a recording, typing the email and password and pressing "Sign in" ended up as steps in the workflow. Now anything done on a login page is simply ignored by the recorder, like a camera that pauses while someone types a password. Workflows no longer contain login steps that were never meant to be replayed.

**Made the Studio ask fewer, smarter questions and stop repeating itself — 2026-10-28**
Before building a workflow, the Studio used to ask three fixed questions every time and then ask the same things again after the build. It also showed a "what you'll probably do" list that nobody needed. Now the assistant reads the recording and writes its own questions, settles what it can by itself, and shows its summary as a normal chat message. A pop-up that needs a decision is always asked about as one, and an answer is never applied to the wrong pop-up.

**Fixed "this recording has no video" appearing right after a good recording — 2026-10-27**
After you finished recording, the chat sometimes said the recording had no video and asked you to record again, even though the video was fine. The Studio moved on before the video file had finished saving, like opening a package before the courier has set it down. It now waits much longer for the video to finish saving, so the recording is read correctly the first time.

**Wrote a plain list of what to fix in the questions and the chat — 2026-10-26**
We read through how the Studio asks its follow-up questions and how the chat answers, looking for weak spots. We found 23, including a chat reply that can vanish after you have read it, an answer that can be applied twice, and a "checking" step that can hold up testing for minutes. The review is a shareable page with a step-by-step plan to fix them and use fewer AI calls, so the Studio gets faster and cheaper to run. Nothing in the product has changed yet.
— 2026-10-26

**Cut the number of AI calls after recording, and made answers safe to retry — 2026-10-26**
After a recording was compiled, the Studio asked the AI several separate questions in a row before showing you anything, and a failure part-way through could lose a chat reply or apply one of your answers twice. Now the compile asks the AI once for everything it needs, the first question appears straight away in plain wording, and the friendlier wording follows a moment later. If an answer fails half way, the workflow goes back to how it was, and a question that was lost because the app closed is rebuilt when you reopen the workflow. Like a clerk who asks the manager one list of questions instead of walking back and forth, and who never files a form twice.
— 2026-10-26

**Wrote a plan for the Studio to understand a recording before building from it — 2026-10-26**
Today the Studio builds a workflow first and only then asks you what you meant, so your answers can only patch what it already guessed. The new plan reverses that: it studies the recording, asks its questions, takes in your answers, and only then builds and tests. We also checked whether a video-watching AI could explain each click from the recording alone. It cannot tell apart clicks that happen less than a second apart, so it would only be an optional helper, switched on per company, for things the browser cannot see. Nothing in the product has changed yet.
— 2026-10-26

**Drew the new "understand first" plan as one shareable picture — 2026-10-27**
The plan for having the Studio study a recording before building from it is now a one-page diagram. It shows each stage in order, who acts at each one (the computer, the AI, or you), and how the questions move from after the build to before it. This makes the idea easy to share with people who won't read the full write-up. Nothing in the product has changed yet.
— 2026-10-27

**Reworked the plan so a video-watching AI explains the recording first — 2026-10-27**
In the new version of the plan, a video AI watches the recording and writes a timed walkthrough of what the person did. The Studio then lines that walkthrough up with its own exact record of each click, and takes snapshots at those moments, instead of asking another AI to choose snapshots. You no longer type a description before recording: you read a short summary of what the Studio saw and confirm or correct it. This only runs for companies that agree to send their recordings for analysis. Everyone else keeps today's flow. Nothing in the product has changed yet.
— 2026-10-27

**Made the video-watching step part of every recording in the plan — 2026-10-27**
The plan used to keep a backup route for when video analysis was switched off or failed. That route is gone: every recording is now watched by the video AI before anything else happens. Very short recordings are stretched to the minimum length the video AI accepts. Nothing in the product has changed yet.
— 2026-10-27

**Plan now sends one combined video instead of one per browser tab — 2026-10-27**
When a recording used several browser tabs, the plan used to send each tab's video separately. Now the tabs are joined into one video in the order they were used, so the video AI sees the whole task from start to finish, switches included. The Studio keeps a note of where each piece came from, so every moment can still be traced back to its tab. Nothing in the product has changed yet.
— 2026-10-27

**Redrew the plan as two tracks running side by side — 2026-10-27**
The plan now handles a recording two ways at once: the video AI watches the video, while the Studio reads its own exact record of every click. The two are then joined into one timeline, and a single AI step works out what the task is for. Because the video AI already describes each step and what the screen looked like, the Studio would no longer need to take snapshots, store pictures in the finished workflow, or ask a separate AI to explain each step. You then get two rounds of questions, one before the workflow is built and one after, before it is tested. Nothing in the product has changed yet.
— 2026-10-27

**Made the recording plan cheaper, safer and quicker to answer — 2026-10-27**
The plan now sends each recording to the video AI once instead of twice, and makes the video smaller and skips long waits before sending it. Its answer is saved, so editing a workflow later never pays for it again. Passwords are blurred before the video leaves the computer, and companies can choose to blur everything typed. Clicks are marked so the video AI notices them, steps can no longer get swapped when matched up, and people answer one round of questions with the likely answers already filled in. Nothing in the product has changed yet.
— 2026-10-27
**Raised the video smoothness in the recording plan — 2026-10-27**
The plan now keeps 10 pictures per second in the video it sends for analysis, up from 5. Quick clicks and brief screen changes are less likely to be missed. The video is still shrunk to a smaller size, so uploads stay fast. Nothing in the product has changed yet.
— 2026-10-27**Built the first half of the new recording-understanding pipeline — 2026-10-27**
The app can now prepare a recording video, send it to the video AI through our cloud, and line up what the video AI saw with what the browser recorded. It can also write a plain summary of the task and a short list of questions, with the likely answers filled in, and it remembers the answers even if the workflow is rebuilt. None of this is connected to the recording screen yet, so people will not notice any change until that last step is done.
— 2026-10-27
**Stopped building a pile of screenshots for every recording — 2026-10-27**
When you built a workflow, the app used to cut five pictures out of the video for every click and ask an AI to look at each one. It no longer does that, because the video AI now watches the whole recording once and the app uses its description instead. Building is faster and cheaper. One catch: the editor screen that shows step pictures is empty for new recordings until it is reconnected to the new way.
— 2026-10-27

**Planned how the Studio decides what to ask you — 2026-10-27**
The plan no longer has a fixed list of questions. The AI reads everything it knows about the recording, decides most things by itself, and only asks you what you alone know or what you must approve, like deleting something. You see a short list of what it decided and can change any line. Your answers are remembered for that app, so you get asked less each time. Nothing in the product has changed yet.
— 2026-10-27
**Explained why the "is this app signed in?" check can give different answers — 2026-10-29**
The three buttons that check a sign-in inside the Studio (Check now, Check all and the check before recording) all use the same check. The part that runs your skills uses a different, separately written check. We wrote a plain-language page comparing the two side by side and listing where they can disagree. Nothing in the product changed yet.
— 2026-10-29

**Made the "is this app signed in?" check give the same answer everywhere — 2026-10-30**
The Studio buttons (Check now, Check all and the check before recording) and the part that runs your skills used to judge sign-in with two different sets of rules, so one could say "signed in" while the other asked you to sign in again. Now both follow the exact same rules, open the same page and use one shared browser that holds every app's sign-in, the way a real run does. They also remember a good answer for the same 10 minutes. It also fixes apps whose sign-in page is the site's home page being wrongly marked as expired.
— 2026-10-30

**Listed every moment the app checks whether you are signed in — 2026-10-30**
We went through the whole product and wrote down each place a sign-in check can happen, in Build Studio and when a skill runs, and whether it opens a real browser or only reads a saved answer. The list is now on the comparison page. It also shows which few checks still use their own rules instead of the shared one. Nothing in the product changed.
— 2026-10-30


**Wrote a plain-language plan for building workflows out of ready-made blocks — 2026-10-30**
We wrote a guide that explains the idea of the product keeping a box of small ready-made actions, and each workflow being just an instruction sheet that arranges them. It shows which blocks already exist, where the product already changes its plan based on answers or surprises, and what is still missing. It also lists a safe order for building the missing parts. Nothing in the product changed.
— 2026-10-30


**Settled the first decisions for the "ready-made blocks" plan — 2026-10-30**
We wrote down four choices: show the full list of steps a run will do before it starts, let answers only switch steps off or repeat them, keep the order of agent first and then a person when something goes wrong, and keep one fixed list of allowed on-the-fly changes owned by Conxa. The plan guide now has a clear first phase to build and a later phase that waits. Nothing in the product changed.
— 2026-10-30


**Added an honest "what can and cannot be automated" section to the blocks plan — 2026-10-30**
The plan now says plainly that not every website workflow can be automated. It lists the missing building blocks we could add, such as calculating a value, calling another workflow, and reading a downloaded file. It also lists what no block can fix, such as CAPTCHA tests and apps drawn as pictures. It ends with a safe sentence to use when describing the product to customers. Nothing in the product changed.
— 2026-10-30


**Added the idea of letting the connected AI assistant do the thinking to the blocks plan — 2026-10-30**
The plan now explains that when a workflow needs real judgement, the product can stop, ask the AI assistant it is running inside, and carry on with a short, checked answer. This already works in a limited form and costs the customer, not us. The plan also lists the safety rules that stay, such as never letting that answer drive an irreversible action, and the cases where nobody is there to answer, like scheduled runs. Nothing in the product changed.
— 2026-10-30


**Rewrote the blocks plan as one clean, easy-to-read guide — 2026-10-30**
The plan had grown by several add-ons and read like a patchwork. It is now one guide in a clear order: the idea, what we already have, what is missing, the choices we made, the rules that stay, an example, the build plan and what can and cannot be automated. The ideas are the same, just easier to follow. Nothing in the product changed.
— 2026-10-30


**Added five new building blocks to workflows, and made the AI review step able to do more — 2026-10-30**
Workflows can now wait for something to happen on a page, copy and read text through the browser clipboard, and run another saved workflow in the middle of the current one. The AI review step can now be used to work out a value, read a downloaded file, or pick between steps, and its answer can be used by later steps. Each new block has safety limits, such as never letting an AI answer decide an irreversible action, and each is covered by automatic tests.
— 2026-10-30

**Every recorded action now becomes its own step, with nothing folded or grouped — 2026-11-02**
Before, the app quietly merged or threw away some of what you did while recording, and showed some steps tucked under others. A recording of 12 actions could come out as 11 steps, like a note-taker skipping lines they thought were repeats. Now each action you record is kept as its own step, shown in a flat list in the order you did it.
— 2026-11-02
