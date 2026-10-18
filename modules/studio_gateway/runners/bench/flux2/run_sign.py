import subprocess,sys
p='A wooden sign standing in a lush green garden with flowers, the sign has carved painted white letters reading exactly "The Lord is my shepherd" and below it a smaller line reading "Psalm 23:1", soft morning sunlight, photorealistic, sharp focus on the sign'
for s in (42,7):
    subprocess.run([sys.executable,r"D:\0_LLM\bench\flux2\flux2_run.py","--prompt",p,"--w","1088","--h","1920","--seed",str(s),"--out-name",f"sign_psalm_s{s}"])
