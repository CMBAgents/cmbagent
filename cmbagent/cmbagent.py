
from .utils import *

from autogen.coding import LocalCommandLineCodeExecutor

import autogen
from autogen import AssistantAgent
from autogen.agentchat.contrib.retrieve_user_proxy_agent import RetrieveUserProxyAgent


import os

from autogen import UserProxyAgent, config_list_from_json
from autogen.agentchat.contrib.gpt_assistant_agent import GPTAssistantAgent







class CMBAgent(object):
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        print(f"Autogen version: {autogen.__version__}")



    def hello_cmbagent(self):
        return "Hello from cmbagent!"





