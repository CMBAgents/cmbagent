# How to run cobaya?

We run cobaya from within the Bash shell using `cobaya-run` command with `-f` flag.

The yaml file is saved via a python script `setup_yaml.py`

To save the yaml file we always use the `dump` method. Hence the python script should look like:

```python
import yaml

...

# Prepare the data
data = {
...
}


# Specify the filename
filename = 'example.yaml'

# Write the data to a YAML file
with open(filename, 'w') as file:
    yaml.dump(data, file, default_flow_style=False)

print(f'Data has been saved to {filename}')
```

The `default_flow_style=False` argument ensures the YAML is written in a human-readable block format.

Assuming that our newly saved `example.yaml` file is a valid coabaya input yaml file we then run the following:


### For an MCMC

```python
import subprocess

def run_mcmc():
    try:
        result = subprocess.run(['mpirun', '-np', '4', 'coabay-run', 'example.yaml', '-f'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("MCMC Output:", result.stdout.decode())
    except subprocess.CalledProcessError as e:
        print("Error running MCMC:", e.stderr.decode())

run_mcmc()
```

### For the evaluate mode

```python
import subprocess

def evaluate():
    try:
        result = subprocess.run(['coabay-run', 'example.yaml', '-f'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("Evaluate Output:", result.stdout.decode())
    except subprocess.CalledProcessError as e:
        print("Error running evaluate mode:", e.stderr.decode())

evaluate()
```


