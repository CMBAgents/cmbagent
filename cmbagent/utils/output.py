

def cmbagent_baseline_output(cmbagent):
    """
    This function is used to generate the baseline output for the CMBAgent.
    """

    # template for one-shot eval
    # Extract the task result from the chat history, assuming we are interested in the executor's output
    try:
        for obj in cmbagent.chat_result.chat_history:
            if obj['name'] == 'executor':
                result = obj['content']
                break
        task_result = result
    except:
        task_result = None

    return task_result
        

