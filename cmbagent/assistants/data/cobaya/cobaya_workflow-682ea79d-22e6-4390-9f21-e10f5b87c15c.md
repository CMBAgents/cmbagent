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

## For an MCMC

```bash
mpirun -np 4 coabay-run example.yaml -f
```


## For the evaluate mode

```bash
coabay-run example.yaml -f
```


# Troubleshooting

if there are issues that seem to be related to cobaya, the `cobaya_assistant` should be consulted first, and if the same sort of issues persist, the `lewis_assistant` should be consulted.

If the issues seem to be related to classy_sz the `class_sz_assistant` should be consulted.

If the issues persist, the `Admin` should be consulted.

