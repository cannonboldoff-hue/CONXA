# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**The built-in browser now shows downloads like a regular browser — 2026-10-24**
Before, clicking a download button gave no sign that anything was happening, so you could not tell when the file was ready. Now a Downloads button appears in the browser, with a progress bar for each file and a clear "Done", "Cancelled" or "Failed" with a retry button. A download that fails or is cancelled is no longer assumed to have worked, and a replay now stops with a plain reason instead of carrying on without the file.
— 2026-10-24

**Clicked items, retries after a failed test, and "record from here" — 2026-10-24**
Until now, if a recording clicked a particular file in a list, the workflow could only ever click that same file. Now Studio can ask whether the item changes each time, and if so it looks for whatever name you give it on that run. After a failed test it also asks whether a value you changed should really vary, and before you record it shows a short checklist of what you will probably do. In Human Edit you can now start recording from any step (the old steps from there on are replaced) or record "another way" (the workflow keeps both and takes whichever the page offers).
— 2026-10-24

**Fixed small gaps that made workflows lose data or stop at the wrong moment — 2026-10-24**
A value the workflow had read could vanish if the run paused to ask a person. Reading a value inside a repeated list only kept the last row. A pause for a risky step could not happen inside a repeated list at all. All three now work, and a missing item can also make the run ask a person instead of just stopping. Dates can be "always 7 days from today" instead of the day you recorded.
— 2026-10-24

**Removed the extra "Human Edit" strip from the top of a workflow — 2026-10-24**
A workflow's page had a thin bar under its title with a lone "Human Edit" link that looked out of place. It is gone, so the page starts straight with the conversation. Human Edit is still one click away from the side panel.
— 2026-10-24

**Fixed "Check all" showing apps as signed in when they weren't — 2026-10-24**
The sign-in check used to count "couldn't tell" as "all good", like a smoke alarm that stays green when its battery is dead. Now an app is shown as signed in only when the check really sees it signed in. If the check can't tell, the app shows an amber warning, and if the app is signed out it shows red with a Reconnect button.
— 2026-10-24

**Added a three-dots menu to every workflow in the left sidebar — 2026-10-24**
Workflows in the sidebar had no way to be renamed or removed unless you opened their group page first. Now a small three-dots button appears when you hover over a workflow, with "Rename" and "Delete" right there. Delete asks you to confirm first, like a "are you sure?" prompt on a shredder.
— 2026-10-24

**Fixed compiling any recording where you scrolled the page — 2026-10-23**
Compiling a recording stopped with a confusing "Compile didn't finish" message whenever you had scrolled the page while recording. One of the clean-up checks didn't know how to look at a scroll, like a sorting machine jamming on an odd-shaped parcel. It now lets scrolls pass straight through, so those recordings compile normally.
— 2026-10-23

**The recording browser now closes the moment you finish — 2026-10-23**
After you clicked "Finish and save", the browser on the right stayed open for several seconds while the recording was being wrapped up and the next step began. Now it disappears the instant you click, and the behind-the-scenes tidying carries on out of sight. It is like switching off the stage lights as soon as the show ends, instead of after the crew has finished packing up.
— 2026-10-23

**Removed the "record, compile and test on the group page" pointers — 2026-10-20**
A workflow's own page used to show a note at the top, and another in the side panel, sending people back to the group page to record, compile and test. Recording already starts right inside the workflow's conversation, so those notes were outdated and confusing. Both are gone; the Human Edit link stays once a workflow has been compiled.
— 2026-10-20

**Moved credits to the top bar and group options to the sidebar — 2026-10-20**
On a group's page, the compile and AI credit counters now sit in the very top bar next to the group's name, instead of taking up a row of their own. Renaming or deleting a group is now done from a small "…" button beside the group's name in the left sidebar, like a file's right-click menu. The page itself starts straight at the list of apps, with nothing in the way.
— 2026-10-20

**Built the cleaner group sign-in screen into the app — 2026-10-20**
The cleaner design for signing in to a group's apps is now the real screen in Build Studio. Apps are a calm list instead of heavy boxes, rarely-used buttons like Rename, Delete and Edit sit behind a small "…" menu, and "New workflow" now waits at the bottom of the list, right where signing in naturally leads. The browser area opens with one short line telling you what it's waiting for.
— 2026-10-20

**Drew a cleaner design for the group sign-in screen — 2026-10-20**
The screen where you sign in to each app in a group was busy: heavy boxes, a crowded row of buttons along the top, and two "windows" stacked inside each other. The new design, modelled on Claude's desktop app, keeps one quiet list of apps on the left and a simple browser panel on the right, with a single clear "Done" button for the app you're signing in to. It's a mockup only; the app itself hasn't changed yet.
— 2026-10-20

**Starting a new workflow now makes you create a group first — 2026-10-20**
Before, someone with no groups could click "New workflow" and invent a group halfway through the form. Now, if there are no groups yet, they are taken straight to creating one, with a short note explaining why. Once a group exists, the form only lets them pick from their existing groups. This keeps every workflow neatly filed from the start, like making a folder before saving a document.
— 2026-10-20

**Page titles moved up into the top bar, with details tucked behind an "i" — 2026-10-19**
Every page used to have its own heading bar under the window's top bar, with a long sentence of explanation beside the title. The title now sits in the top bar itself, and the sentence appears when you hover over a small "i" next to it. Pages that need buttons up top still keep a slim row for them. This gives every screen more room, like trading a thick book cover for a bookmark.
— 2026-10-19

**Removed the "New workflow" title strip from the top of that screen — 2026-10-19**
The screen for starting a new workflow had a heading bar across the top that repeated what the left menu already said. It is gone, so the form now begins right under the window's top bar, matching the Chat screen. Nothing about how the form works has changed.
— 2026-10-19

**Made the top bar blend into the Chat screen — 2026-10-19**
The strip along the top of the window used to be a darker shade than the chat area below it, so the two looked like separate pieces. It now matches the colour beneath it, with the logo corner matching the left menu. The whole window reads as one smooth surface, like a single sheet of paper instead of two taped together.
— 2026-10-19

**Cleaned up the top of the Chat screen — 2026-10-19**
A new chat used to show a "New chat" label across the top, with a thin line under it that added nothing. That label is gone, so the screen starts straight with the question. The thin line under the window's top bar is gone too, so the top of the app looks like one smooth strip.
— 2026-10-19

**Build Studio now opens on the Chat screen — 2026-10-19**
When you opened the app, it landed on the list of groups, which looked empty and gave you nothing to do. Now it opens on a fresh chat asking what you want to automate, like a front door that leads straight into the work. The group list is still one click away in the left menu.
— 2026-10-19

**Creating a group now asks which apps it signs in to — 2026-10-19**
Making a new group used to ask only for a name, and you had to find the sign-in setup yourself afterwards. Now it takes two short steps: name the group, then list the apps it uses. Choosing "Create group & sign in" opens the first sign-in straight away, so you never have to hunt for the Connect button. The sign-in browser now opens beside the app list, as if the list is handing off to it from left to right, with a status message above it that turns green once you are connected.
— 2026-10-19

**Designed how creating a group leads straight into signing in — 2026-10-19**
Making a new group now asks which apps it signs in to, right away, instead of leaving that for later. Pressing "Connect" then opens a private browser beside the app list, where you sign in once and Conxa remembers it. This is a design only for now, laid out as six screens from naming the group to being ready to record.
— 2026-10-19

**Made "New Workflow" on a group page open the full New workflow screen — 2026-10-19**
Clicking "New Workflow" on a group's page used to pop up a small box asking for only a name and a web address. Now it takes you to the same New workflow screen as the left menu, with that group already chosen. This way every workflow is set up the same way, including the optional task description.

**Tidied the left menu and added a profile card — 2026-10-19**
The left menu had a "More" section with three extra links, which made it feel crowded. Build Installer now sits right under Publish, and the Human Edit and All workflows links are gone from the menu. At the bottom there is now a small profile card showing your initials, your name and "Local workspace", like the account switcher in most chat apps.

**Put the connection dot next to your profile name instead — 2026-10-19**
The small status dot now sits at the right end of your profile row at the bottom of the side menu, in place of the up-down arrows. Hover over it to see whether you're connected, and click the row to open your settings. This keeps the status near your account and the top bar clear.

**Moved the connection status to a small dot in the top bar — 2026-10-19**
The "Connected" line at the bottom of the side menu is now a small coloured dot in the top-right corner of the window, beside the refresh button. Hover over it to see what is going on, such as whether you're online or the recording engine is running. It keeps the side menu tidy while the status is still one glance away.

**Removed the empty bar at the top of the main area — 2026-10-19**
On pages with no title, such as the welcome page, a blank strip with a line under it sat across the top of the screen. It now only appears when a page has something to show there. Those pages look cleaner and start higher on the screen.

**Removed the extra Settings button from the side menu — 2026-10-19**
The bottom of the side menu had a Settings button right above your profile, and both opened the same screen. We removed the duplicate, so only the profile row is left. It's a tidier menu with nothing lost.

**Moved the refresh button to the top bar — 2026-10-19**
The refresh button used to sit in the page header, far from the window controls. It now sits in the very top bar, just left of the Browser button. It works the same way, reloading what's on the current page.

**Moved the Conxa logo and menu controls into the top bar of Build Studio — 2026-10-19**
The very top of Build Studio now shows the Conxa logo and name, a button to hide the side menu, and back and forward arrows, with a Browser button on the right beside the window buttons. Before, the logo sat inside the side menu and the top bar only had the window buttons. It now looks like the design you shared and gives you quick navigation from anywhere.

**Conxa Execute can now find the CONXA runtime on a developer's computer — 2026-10-18**
Execute kept saying the runtime wasn't installed, because the test copy developers build lives in a different place than the one Execute looks in. A small new helper now points Execute at the newest test build, and it needs to be re-run after each rebuild. We also fixed the test-build script that left an invisible stray character in a settings file, which made the runtime refuse to start with no explanation.
— 2026-10-18

**Fixed the developer script that builds the local test copy of the CONXA runtime — 2026-10-18**
On many Windows computers the script crashed halfway because it left an invisible stray character at the start of a settings file, and the next tool refused to read it. It now writes that file cleanly, so the build finishes and the test environment gets its runtime. Without this, developers could not try out a recorded workflow on their own machine.
— 2026-10-18

**Conxa Execute no longer opens to a blank black window when its server can't be reached — 2026-10-18**
On startup the app asks the CONXA servers who you are and waits for the answer, with no time limit, so an unreachable server left a black screen forever. It now gives up after 15 seconds and shows the "couldn't start" screen with a Retry button. When developers test it on their own computers, it also talks to their own local server instead of the live one.
— 2026-10-18

**Conxa Execute no longer makes you sign in while developers are testing it — 2026-10-18**
When the team runs Execute on their own computers to test it, it used to stop at a sign-in screen every time. Now it skips that step and signs in as a made-up test user automatically. This never applies to the version customers install, so real sign-in is unchanged for them.
— 2026-10-18

**Build Studio now says clearly when something is unavailable — 2026-10-18**
If your internet drops, a thin banner says what waits and what still works, and the buttons that need the cloud are switched off until it returns. If Build Studio's helper program stops, you get a plain notice with a Restart button instead of an error box, and your workflows are safe. A test that stops because a saved sign-in ran out is now paused rather than marked as failed, so nothing tries to "fix" a workflow that was never broken.
— 2026-10-18

**Settings is now a pop-up, new users get a welcome page, and leaving an edit asks first — 2026-10-18**
Settings opens over whatever you were doing instead of taking you to another page, and shows your usage as AI credits. A brand-new workspace now opens on a short welcome page that explains the three steps and checks that this computer is ready. If you try to leave the step editor with a change you haven't saved, it asks whether to save it, throw it away, or keep editing.
— 2026-10-18

**Test runs now show their browser inside Build Studio — 2026-10-18**
When you test a workflow with the browser visible, the page now appears in a panel beside the conversation instead of in a separate window. When the test ends, the last page stays on screen so you can check the result yourself, and you close it when you are done. Recording and signing in still open their own window, because they need things the built-in panel cannot give them.
— 2026-10-18

**The editing screen now sits inside the conversation — 2026-10-18**
When you open a workflow's step editor from its conversation, you can chat about the selected step right beside it, and the assistant's suggested changes appear as cards you can apply or undo. The steps list now leads with each step's name and a small coloured tag for what kind of action it is. The main button there now says Test changes, because approving happens after a test passes, back in the conversation.
— 2026-10-18

**Tests now stop before anything real happens, and can fix themselves — 2026-10-18**
A test run now pauses before any step that can't be undone, such as sending a real invoice, and waits for you to either run those last steps for real or end the test there. If a test fails, Build Studio can make one safe change, show it to you with an Undo, and try again up to three times before asking for help. Approving a workflow is still your decision, and approving never publishes anything. We also repaired a broken file in the part that runs workflows, which had stopped it from starting at all.
— 2026-10-18

**After recording, Build Studio now compiles on its own and asks you a few questions — 2026-10-18**
When you finish recording, the workflow is turned into steps automatically, with a progress card that only shows real progress. If another workflow is already compiling, yours waits its turn and says so. Then the assistant asks up to five short questions it can't answer by itself, like whether to ask before sending something that can't be undone, and every answer is applied through the same safe editing tools a person would use.
— 2026-10-18

**You can now sign in and record a workflow from its conversation — 2026-10-18**
Starting a workflow now opens its conversation, which signs you in first if the app needs it and then records, showing each action in plain words as you go. You can pause recording, discard it, or save what you have if the browser closes by accident. When you type a value you can say whether it changes each time, so you are asked less later.
— 2026-10-18

**Gave every workflow its own conversation, and made approved mean approved — 2026-10-18**
Each workflow now has one running conversation that remembers what happened to it, and there is a general chat for planning a workflow before recording it. The assistant can suggest a change as a card you can apply or undo, and nothing changes until you press the button. We also fixed a mix-up where any edit, even an automatic one, made a workflow look approved; now an edit takes the approval away until a person approves again.
— 2026-10-18

**Gave Build Studio its new look and layout — 2026-10-18**
Build Studio now has a side menu that lists every group and every workflow inside it, each with a small symbol showing whether it is fine, needs you, failed, running or not started. A details panel on the right shows where the open workflow is in its journey, and both side panels can be resized or folded away with the keyboard. It is like moving from a pile of separate pages to one workbench where everything is within reach. The conversation itself, which is the next big piece, is not built yet.
— 2026-10-18

**Planned the new conversation-style Build Studio, step by step — 2026-10-18**
Before changing anything, we read how the app works today and turned the new design into a phase-by-phase plan that was approved. The plan keeps every existing safety rule, and it adds a pause that stops a test just before a step that can't be undone, like sending a real invoice. It also gives one clear name, "AI credits", to the allowance that chat and editing both use. Think of it as the building plans signed off before the first wall goes up.
— 2026-10-18

**Made the Build Studio design safer and easier to follow — 2026-10-18**
Tests no longer do anything that can't be undone, like sending a real invoice, without asking first, and that includes the automatic retries after a failure. Unfinished workflows now show up inside their group from the start, instead of hiding in a separate list. Messages now name steps ("Send invoice") instead of numbers that change as steps are added. We also added a pause button and a way to flag changing values while recording, plus a few smaller clean-ups.
— 2026-10-18

**Wrote the build plan for the new Build Studio — 2026-10-18**
We wrote a detailed set of instructions that an AI assistant can follow to build the new conversation-style Build Studio. It covers the screens, the chat that remembers each workflow's history, the safety rule that tests never send anything real without asking, and the automatic fix-and-retest loop. It is split into steps, and the assistant must stop and get approval on its plan before writing any code.
— 2026-10-18

**Drew a more polished left-hand menu for Build Studio — 2026-10-19**
The left-hand menu in Build Studio worked, but it felt more like a list than a professional tool. We drew a new version with a workspace switcher at the top, one search box, and clearer group headings. Groups now show at a glance which workflows are ready, which need attention, and which have failed. Credit usage and your connection to the cloud now sit together at the bottom, so nothing important is hidden. This is a design mock-up only; the app itself has not changed yet.
— 2026-10-19

**Made the new left-hand menu design calmer and less crowded — 2026-10-19**
The first draft of the new menu had too much on screen at once. We removed the recent chats list, the extra notes under each workflow, the coloured counters and the credits box. A small warning mark now appears only when something actually needs attention. Credit details moved into the workspace menu, so the menu stays clean but nothing is lost.
— 2026-10-19

**Took the workspace switcher out of the new menu design — 2026-10-19**
The menu design had a switcher for moving between workspaces, but every account only ever has one workspace. We removed it, so the top of the menu now just says "Build Studio". It is one less thing to click and one less thing to wonder about.
— 2026-10-19

**Designed a cleaner, more professional home screen for Build Studio — 2026-10-20**
The first screen people see in Build Studio was mostly empty, with the message box pushed to the very bottom. A new design puts the message box in the middle, adds four ready-made starting ideas, and shows the last few workflows you touched so you can carry on with one click. This is a design mock-up only; the app itself has not changed yet.
— 2026-10-20

**Removed the recent workflows list from the new home screen design — 2026-10-20**
The home screen design had a list of your last few workflows under the starting ideas. It wasn't needed, so we took it out. The screen now shows just the question, the message box and the four starting ideas.
— 2026-10-20

**Brought the new home screen design into Build Studio's chat — 2026-10-20**
Starting a new chat used to show a short heading at the top and a small message box stuck to the bottom of an empty screen. The message box now sits in the middle, larger and easier to spot, with four starting ideas underneath that fill in a ready-written request when clicked. A short note under the box makes clear the chat can only read, never change anything. Once the conversation starts, the box moves back to the bottom as before.
— 2026-10-20

**Designed recording inside Build Studio instead of a pop-up browser — 2026-10-20**
Pressing "Create and start recording" used to open a separate browser window, which was easy to lose behind other windows. The new design opens the browser inside Build Studio, on the right side of the conversation, so you can see each step being captured as you go. Sign-ins, new tabs a website opens, pausing and finishing all happen in that same panel. This is a design mock-up for now; the app itself hasn't changed yet.
— 2026-10-20

**Proved Build Studio's own browser can record, with video — 2026-10-20**
Before moving recording into the browser panel inside Build Studio, we tested every piece it depends on: capturing clicks in every part of a page, pop-up tabs, a fixed desktop page size, saving sign-ins, and the step-by-step video. Everything worked, and the video's timing came out slightly more accurate than today's separate-window recorder. The video-making piece is now built into the app, ready for the next step, but recording still uses the separate window for now.
— 2026-10-20

**Build Studio's browser panel can now host recordings and sign-ins — 2026-10-20**
The browser panel inside Build Studio used to be built only for test runs. It can now also hold a recording or a sign-in, always showing websites at the same desktop size and shrinking the picture to fit the panel. When a website opens a pop-up, it now appears as a tab in the panel and stays linked to the page that opened it, which sign-in pop-ups rely on. Recording itself still opens the separate window until the next step is finished.
— 2026-10-20

**Recording can now run inside Build Studio's own browser — behind a switch — 2026-10-20**
The recorder can now capture a workflow inside the browser panel in Build Studio instead of a separate window, including the step-by-step video. We recorded the same session both ways and compared everything the app builds from it: the captured steps, the pictures for each step and the finished workflow all matched. Along the way we fixed a download being recorded before the click that started it. It stays switched off until the recording screens are finished.
— 2026-10-20

**Recording and signing in now happen inside Build Studio, not in a pop-up window — 2026-10-20**
After you fill in a new workflow and press "Create and start recording", the website now opens in a browser on the right side of Build Studio instead of a separate window. If the site needs you to sign in first, you sign in right there, then recording starts. You can pause, widen the browser, and finish from the same place, and a red "Recording" badge stays at the top so you always know it's on. Signing in from a group's page also happens inside that page now.
— 2026-10-20

**The browser now gets more room when it opens — 2026-10-20**
When the browser opens inside Build Studio, the menu on the left now slides out of the way so the browser can be bigger. This happens for tests, sign-ins and recordings alike. When you hide or close the browser, the menu comes back exactly as it was. If you need the menu while the browser is open, the menu button at the top brings back a slim version of it.
— 2026-10-20

**The browser inside Build Studio now works like Chrome — 2026-10-20**
The browser inside Build Studio had no way to open or close tabs yourself, and you couldn't type a website into it. Now it has a proper tab bar with a plus button and a close button on each tab, an address bar where you can type a website or just search, and a right-click menu for copy, paste and opening links in a new tab. The usual keyboard shortcuts work too, like Ctrl+T for a new tab and Ctrl+W to close one. Anything you do this way while recording is captured as part of the workflow.
— 2026-10-20

**Recording now makes sure you're signed in to every app first — 2026-10-20**
Pressing Start Recording could open the browser while you were signed out of an app the workflow needed later, like Google Drive in a workflow that starts on GitHub. Studio only checked the app the workflow starts on, and throwing away a recording also wrongly marked every sign-in as freshly checked. Now Studio checks every app in the workflow's group before the browser opens, and asks you to sign in to each one that's missing or expired, one after another. Recording only starts once they're all signed in, so you never find out halfway through.
— 2026-10-20

**Signing in inside Studio now shows the page and finishes by itself — 2026-10-20**
When Studio asked you to sign in to an app, the browser on the right could stay completely blank, so there was nothing to sign in to. That's fixed, and you no longer have to press "I'm signed in": Studio notices the moment you've signed in and goes straight on to the next app or the recording. Cancelling a sign-in also used to wipe the sign-in you had saved before; now it leaves it alone.
— 2026-10-20

**Started teaching recordings to remember the whole popup, not just the button you pressed — 2026-10-24**
When a popup like "this file already exists" appeared during recording, the system only remembered the one button you clicked. We added the empty slot where the popup's title, message and every button will be saved, so a later step can tell a real choice from a harmless popup. Nothing changes for users yet, because the recorder does not fill the slot in yet.

**The recorder now saves the whole popup when you click inside one — 2026-10-24**
When you press a button in a popup such as "this file already exists", the recording now keeps the popup's title, its message and every button it offered, and marks the one you pressed. Browser alert boxes get the same treatment. Nothing about how workflows run has changed yet. This is the groundwork that lets the next steps tell a real decision apart from a harmless popup.


**Workflows now understand "this file already exists" and other pop-ups that need a decision — 2026-10-24**
Before, if a pop-up asked "Replace, Keep both or Cancel?" while you recorded, the workflow quietly pressed whatever button you had pressed, every time, even if that was "Replace". Now it treats the pop-up as a real choice. A harmless choice you made is repeated, but anything that could overwrite or delete things stops and asks a person first, and a pop-up it has never seen is never answered on its own.

**You can now tell a workflow what to do when a pop-up appears — 2026-10-24**
After a recording, Build Studio asks one plain question per decision pop-up: always press one button, let the person running it choose each time, pause and ask, or stop. Pressing a risky button there is what lets it run without asking. Until you answer, risky choices wait for a person.

**You can record the "no pop-up" case without losing your first recording — 2026-10-24**
If a pop-up only appeared because a file already existed, you can now record the task again with a new file so no pop-up shows. Build Studio compares the two recordings and learns that the pop-up is optional, without recompiling anything, using any credits, or touching your first recording or your answers.

**Testing a workflow now asks you when a pop-up needs a decision — 2026-10-24**
During a test, a pop-up that needs a choice shows up on the test card with a button for each option. Only you can answer it; the AI helper is never asked to pick. If you end the test there, nothing more runs.

**The workflow can no longer close a "choose one" pop-up by accident — 2026-10-24**
When a click was blocked by an unexpected pop-up, the safety net used to press Escape or a close button, which on a "Replace or Cancel?" box quietly cancels the whole action. Now it leaves pop-ups that ask you to choose alone, and still clears cookie banners and plain "close this" notices as before.

**Build Studio now keeps a private map of every app your workflows touch — 2026-10-24**
Each time you record and compile a workflow, Studio now remembers which screens of the app it visited, which buttons were pressed, and which pop-ups showed up along the way. Workflows on the same app add to the same map instead of starting from scratch, and re-compiling or deleting a workflow updates it correctly. The map keeps only the shape of the app, never what you typed or what was on the page, so it is safe to build on later.
— 2026-10-24

**Teammates can now share what Studio has learned about an app — 2026-10-24**
The private map Studio keeps of each app can now be shared with the rest of your team. After you publish, Studio sends your latest additions to Conxa Cloud and picks up what your teammates have added, so everyone builds on the same picture. Only the outline of the app is shared, never anything you typed or any page content, and each company only ever sees its own.
— 2026-10-24

**New "App maps" page shows what Studio has learned about each app — 2026-10-24**
There is now an App maps page in the left menu. Pick an app and you see its screens as boxes, the things that were done as arrows between them, and a yellow badge wherever a pop-up showed up. Click anything to see which workflows use it and, for a pop-up, how often it appeared and which button people chose.
— 2026-10-24

**Studio now warns you about pop-ups that other workflows ran into — 2026-10-24**
If another workflow on the same app has met a pop-up such as "this file already exists" after a certain button, Studio now says so while you record that button, and asks after you finish whether to cover it. If you say yes, the workflow stops and asks a person which button to press whenever that pop-up shows up. It never picks a button for them.
— 2026-10-24

**Studio can now remember your standing answer for a pop-up, and runs follow it safely — 2026-10-24**
On the App maps page you can tell Studio what to do whenever a particular pop-up shows up in an app, for example always press "Keep both". After you publish, a workflow that meets that pop-up, even one it was never recorded with, follows your answer. Buttons that change or remove things need an extra tick before they can run without asking, and if no answer is set, a person is still asked.
— 2026-10-24

**Studio now learns about pop-ups your customers actually run into — 2026-10-24**
When a workflow on a customer's computer hits a "choose one" pop-up it was never taught about, it now reports the outline of that pop-up (a few title words and the button names) back to you. The App maps page then shows pop-ups that real runs met but no workflow covers yet, so you can decide what should happen. It never sends file names, numbers, typed text or the pop-up's message.
— 2026-10-24

**AI assistants can now tell which website each workflow is for — 2026-10-24**
When an AI assistant looks at the workflows installed on a computer, each one now says which website it works in, for example "works in drive.example.com". That helps the assistant pick the right workflow without guessing from its name. Nothing about the website's pages or your data is shared to make this work.
— 2026-10-24

**Wrote a review of what's still missing for a fully chat-driven Studio — 2026-10-24**
We checked how close Studio is to letting someone build a workflow purely by chatting: describe it, record it, answer questions, test it. Most of the pieces are already built. The main gaps are that typed requests like "test it again" don't trigger anything yet, the task the person described is never checked against what they recorded, and workflows can't hand information back to whoever ran them. No product behavior changed; this is a written plan, ranked by importance.
— 2026-10-24

**Found why the assistant starts testing before it understands the task — 2026-10-24**
When someone describes a task and records it, the assistant only asks questions about text they typed while recording. It never compares the recording with what they said they wanted, and it can't turn a chosen page or a clicked item into something to ask about. So a workflow like "copy a file from one site to another" is compiled with the exact page and file baked in, and the assistant says it has nothing to ask. This was a read-only investigation; nothing was changed yet.

**The assistant now checks a recording against what you asked for, before it offers a test — 2026-10-24**
Before this change, a workflow made only of clicks and page visits was saved with nothing to fill in, and the assistant said it had nothing to ask. Now it reads your description, works out which choices (like which repository or which record) look specific to the recording, and asks you whether they should change each run. Say yes and that part becomes something you fill in when you run it. Testing now waits until those questions are answered.

**You can now tell the assistant what to do in plain words — 2026-10-24**
Before, typing "test it" or "approve" in the chat got you a polite reply and nothing else, because only buttons could move the workflow forward. Now the assistant understands those commands, but only for the steps that make sense at that moment. Anything that records, spends a credit, sends real data or approves asks you to press Confirm first. A row of buttons at the bottom of every workflow shows the same next steps, and the general chat now knows your workflows so it can take you to the right one.

**Fewer questions get lost, and the assistant now explains where recorded steps went — 2026-10-24**
When a recording had more than five things worth asking about, the extras used to vanish without a word. Now they wait in line, and you can ask for them whenever you like. When a ten-action recording becomes nine steps, the assistant says one was folded away instead of leaving you to wonder. Workflows also stop asking for values that no step actually uses, and re-compiling the same recording gives the same answer as before.

**Workflows can now read a value off the page and hand it back, and skip steps that don't apply — 2026-10-24**
Until now a workflow could only do things; it couldn't tell you what it found, like a ticket number or an order total. While recording you can now press "Capture a value", click the number you care about, and the finished workflow returns it to whoever ran it. Steps can also be switched off when an optional input is left blank, and a step aimed at one row in a list is quietly skipped (and reported) when that row isn't there, instead of stopping the whole run.

**Groundwork for fixing just one part of a workflow, and a picture of what went wrong — 2026-10-24**
When a test fails, the assistant now looks at a screenshot of the page at that moment, and the failure card shows you the same picture, so "the button moved" is something it can see rather than guess. We also built the pieces for re-recording only the later part of a workflow without losing your earlier answers: replay to a chosen step, swap in the new steps, and keep what still applies. The last piece — letting the recorder take over the paused browser — still needs a live check in the app, so it isn't switched on yet.

**A test that paused on an expired sign-in now carries on after you sign in again — 2026-10-24**
Before, a test that stopped because a saved sign-in had run out just showed a "Test again" button that didn't sign you in, so you had to go find the sign-in page yourself. Now there is a "Sign in and test again" button that signs you in and runs the same test with the same inputs. Tests that were cut short by closing the app are now labelled as interrupted instead of showing a spinner forever, and a "go ahead?" check in front of a step that can't be undone can no longer be edited into an open-ended question by accident.
