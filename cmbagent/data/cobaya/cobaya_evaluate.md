# Setting up a yaml parameter file for cobaya evaluate mode

Here how to structure the yaml file when we are asked about evaluating a likelihood at some parameter values.

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
        'param1': {
            'prior': {
                'min': 0.0,
                'max': 10.0
            },
            'ref': {
                'dist': 'norm',
                'loc': 4.1267026,
                'scale': 1.0
            },
            'proposal': 0.1,
            'latex': 'param_1'
        },
        'param2': {
            'prior': {
                'min': 0.0,
                'max': 10.0
            },
            'ref': {
                'dist': 'norm',
                'loc': 6.4,
                'scale': 1.0
            },
            'proposal': 0.1,
            'latex': 'param_2'
        },
        'param3': {
            'prior': {
                'min': 0.0,
                'max': 10.0
            },
            'ref': {
                'dist': 'norm',
                'loc': 0.3,
                'scale': 1.0
            },
            'proposal': 0.1,
            'latex': 'param_3'
        }
    },
    'prior': {
        'param1_prior': "lambda param1: stats.norm.logpdf(param1, loc=1.0, scale=0.013)"
    },
    'sampler': {
        'evaluate': {
            'override': {
                'param1': 1.2,
                'param2': 3.5,
                'param3': 6.4
            }
        }
    },
    'timing': True,
    'debug': False
}

# Serializing the dictionary to a yaml formatted string
yaml_str = yaml.dump(config, default_flow_style=False)

# Writing the yaml string to a file
with open('config.yaml', 'w') as file:
    file.write(yaml_str)
    
```

The main idea is to have the sampler block set to

```
sampler:
    evaluate:
        override:
           param1 : 1.2
           param2 : 3.5
           param3 : 6.4
```

Unless otherwise stated, we always specify

```
timing: true
```

so we can see the evaluation time.

We also set `debug: False` to avoid printing out execution messages.

