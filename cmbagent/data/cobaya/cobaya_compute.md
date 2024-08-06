# How to run cobaya?

We run cobaya from within python block using `cobaya-run` command with `-f` flag.

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

Assuming that our newly saved `example.yaml` file is a valid cobaya input yaml file we then run the following:




