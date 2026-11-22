# Stand-in default browser for Build Studio in WF-19: logs the URL and follows it like a person who approves.
import sys, urllib.request, time
open("C:/Users/kiran/Desktop/Conxa/docs/testing/evidence/WF-019/studio-fake-browser.log", "a").write(f"{time.strftime('%H:%M:%S')} opened {sys.argv[1][:150]}\n")
try: print(urllib.request.urlopen(sys.argv[1], timeout=20).status)
except Exception as e: print("ERR", e)
