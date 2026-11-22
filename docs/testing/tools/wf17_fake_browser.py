# Stand-in for the default browser (BROWSER env): follows the authorize URL like a person who approves, then exits.
import sys, urllib.request, time
open("C:/Users/kiran/Desktop/Conxa/docs/testing/evidence/WF-017/fake-browser.log", "a").write(f"{time.strftime('%H:%M:%S')} opened {sys.argv[1][:120]}\n")
try: print(urllib.request.urlopen(sys.argv[1], timeout=20).status)
except Exception as e: print("ERR", e)
