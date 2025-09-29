import subprocess

# Define the shell command you want to run
cmd = "yourbench run --config ./my_example.yaml"
subprocess.call(["yourbench", "run", "--config=./my_example.yaml"])

# Create a subprocess.Popen instance
#process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# Wait for the command to complete and capture the output
#stdout, stderr = process.communicate()

# Check the return code to see if the command succeeded
#return_code = process.returncode

# Print the captured output and return code
#print("Standard Output:\n\n")
#print(stdout.decode("utf-8"))
#print("Standard Error:\n\n")
#print(stderr.decode("utf-8"))
#print(f"Return Code: {return_code}")
