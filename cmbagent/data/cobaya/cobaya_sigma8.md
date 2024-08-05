Never set `A_s`, `As`, or `logA` in a parameter block if `sigma8` is used.

For instance: 

```
params:
  sigma8:


  A_s:

```

is WRONG. 
 


```
params:
  sigma8:


  logA:

```

is also WRONG. 

In all these cases you should ONLY SET SIGMA8.