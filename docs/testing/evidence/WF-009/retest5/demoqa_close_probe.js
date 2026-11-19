const { chromium } = require("C:/Users/kiran/Desktop/Conxa/runtime/node_modules/playwright");
(async()=>{const b=await chromium.launch({headless:true});const p=await b.newPage({viewport:{width:1280,height:900}});
const errs=[];p.on("pageerror",e=>errs.push(e.message.slice(0,100)));
await p.goto("https://demoqa.com/automation-practice-form",{waitUntil:"load"});await p.waitForTimeout(2500);
await p.fill("#firstName","A");await p.fill("#lastName","B");await p.fill("#userNumber","9876543210");await p.click("label[for=gender-radio-2]");
await p.click("#submit");await p.waitForSelector(".modal-content");await p.waitForTimeout(1500);
const open=()=>p.evaluate(()=>!!document.querySelector(".modal.show,[role=dialog]"));
console.log("open",await open());
await p.click("#closeLargeModal");await p.waitForTimeout(1500);console.log("after close",await open(),errs);
if(await open()){await p.keyboard.press("Escape");await p.waitForTimeout(800);console.log("after esc",await open());}
await b.close();process.exit(0)})().catch(e=>{console.log("ERR",e.message.slice(0,200));process.exit(1)});
