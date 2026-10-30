# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

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
