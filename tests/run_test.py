import subprocess
import os
import sys

def test_multi_env():
    # We want to run dcj with --env-file tests/.env and --env-file tests/.env.docker
    # against tests/docker-compose.j2 and dump the output to verify.
    
    cmd = [
        sys.executable, "dcj.py",
        "--env-file", "tests/.env",
        "--env-file", "tests/.env.docker",
        "--yml-file", "tests/docker-compose.test.yml",
        "--dump"
    ]
    
    # We need to make sure we are in the directory where tests/docker-compose.j2 is visible 
    # OR we use a template that is found. dcj looks in current dir for default templates.
    # Let's copy the template to current dir for a moment or run from project root and specify it.
    # dcj currently finds templates in CWD.
    
    print(f"Running command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print("Error running dcj:")
        print(result.stderr)
        sys.exit(1)
        
    output = result.stdout
    print("Rendered Output:")
    print("---")
    print(output)
    print("---")
    
    # Check for some expected values from both env files
    # From .env: SMTP_HOSTNAME="smtp-relay.local"
    # From .env.docker: SMTP_DOCKER_NAME="smtp-relay"
    
    if "smtp-relay.local" in output and "container_name: smtp-relay" in output:
        print("SUCCESS: Both env files were loaded and used in rendering.")
    else:
        print("FAILURE: Missing expected values in output.")
        sys.exit(1)

    # Now run without --dump to verify it outputs a file
    cmd_file = [
        sys.executable, "dcj.py",
        "--env-file", "tests/.env",
        "--env-file", "tests/.env.docker",
        "--yml-file", "tests/docker-compose.test.yml",
        "config" # Use config command to avoid actually starting anything
    ]
    print(f"Running command: {' '.join(cmd_file)}")
    result_file = subprocess.run(cmd_file, capture_output=True, text=True)
    
    if os.path.exists("tests/docker-compose.test.yml"):
        print("SUCCESS: Output file 'tests/docker-compose.test.yml' was created.")
    else:
        print("FAILURE: Output file 'tests/docker-compose.test.yml' was not created.")
        sys.exit(1)

if __name__ == "__main__":
    test_multi_env()
