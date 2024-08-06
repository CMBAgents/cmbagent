# How to make yaml configuration file for cobaya?


The yaml file is saved in a pythn code block. 

To save the yaml file we always use the `dump` method. Hence the python code should look like:

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




