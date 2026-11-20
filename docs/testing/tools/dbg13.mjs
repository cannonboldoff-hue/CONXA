import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const cdp = await s.context().newCDPSession(s);
await cdp.send("Debugger.enable");
await cdp.send("Debugger.setPauseOnExceptions", { state: "all" });
let n=0;
cdp.on("Debugger.paused", async (e) => {
  n++; const f=e.callFrames[0];
  console.log("PAUSE", e.reason, e.data?.description?.slice(0,200), f.functionName, f.url.slice(-60), f.location.lineNumber);
  try{ for(const f2 of e.callFrames.slice(0,4)){ const r=await cdp.send("Debugger.evaluateOnCallFrame",{callFrameId:f2.callFrameId,expression:"typeof values!=='undefined'?JSON.stringify(Object.keys(values).filter(k=>values[k]===undefined||values[k]===null)):'novalues'"}); console.log(' frame',f2.functionName,r.result.value);} }catch(x){console.log('evalerr',String(x).slice(0,100))}
  await cdp.send("Debugger.resume");
});
await s.getByRole("button", { name: /^Save step/ }).click({noWaitAfter:true}).catch(e=>console.log(String(e).slice(0,100)));
await new Promise(z=>setTimeout(z,3000));
await cdp.send("Debugger.setPauseOnExceptions", { state: "none" });
await cdp.send("Debugger.disable");
console.log("pauses",n);
await b.close();
