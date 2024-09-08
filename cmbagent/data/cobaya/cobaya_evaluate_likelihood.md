# How to evaluate a likelihood with cobaya?

Here how to structure the code when we are asked about evaluating a likelihood at some parameter values.

## setting up configuration file with yaml package via python

First, we write a yaml file. Then we use subprocess.run to run cobaya. 

```python
import yaml


# Setting up a dictionary to represent the yaml structure
config = {
    'theory': {
        'my_theory': {
            'extra_args': None,
            'stop_at_error': True
        }
    },
    'likelihood': {
        'my_likelihood': None
    },
    'output': '/path/to/chains_directory/chains_root',
    'params': {
        'param1': 0.1,
        'param2': 2.3
        }
    ,
    'prior': {
        'param1_prior': "lambda param1: stats.norm.logpdf(param1, loc=1.0, scale=0.013)"
    },
    'sampler': {
        'evaluate': 
    },
    'timing': True,
    'debug': False
}

# Serializing the dictionary to a yaml formatted string
yaml_str = yaml.dump(config, default_flow_style=False)

# Writing the info to a yaml file
with open('config.yaml', 'w') as file:
    file.write(yaml_str)
    
# run cobaya to avaluate the likelihood
try:
    subprocess.run(['cobaya-run', 'config.yaml', '-f'])
except Exception as e:
    print(f"Error running evaluate mode: {e}")
    
```

The main idea is to have the sampler block set to

```
'sampler' :{
    'evaluate':
    }
```

Unless otherwise stated, we always specify

```
timing: True
```

so we can see the evaluation time.

We also set `debug: False` to avoid printing out execution messages.

