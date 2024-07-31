
from .utils import *
from .assistants.classy_sz import classy_sz_agent
from .assistants.classy import classy_agent
from .assistants.camb import camb_agent
from .assistants.cobaya import cobaya_agent
from .assistants.getdist import getdist_agent

from .assistants.act import act_agent
from .assistants.planck import planck_agent

from cmbagent.engineer.engineer import engineer_agent
from cmbagent.planner.planner import planner_agent
from cmbagent.executor.executor import executor_agent



logger = logging.getLogger(__name__)

class CMBAgent(object):
    def __init__(self, 
                 cache_seed=42, 
                 temperature=0,
                 timeout=1200,
                 gpt4o_api_key=None,
                 **kwargs):
        
        self.kwargs = kwargs

        self.work_dir = work_dir
        
        logger.info(f"Autogen version: {autogen.__version__}")
        # print(f"Autogen version: {autogen.__version__}")


        gpt4o_config = config_list_from_json(f"{path_to_apis}/oai_gpt4o.json")
        if gpt4o_api_key is not None:
            gpt4o_config[0]['api_key'] = gpt4o_api_key


        logger.info(f"Path to APIs: {path_to_apis}")

        llm_config = {
                        "cache_seed": cache_seed,  # change the cache_seed for different trials
                        "temperature": temperature,
                        "config_list": gpt4o_config,
                        "timeout": timeout,
                    }
        
        logger.info("LLM Configuration:")

        for key, value in llm_config.items():
            logger.info(f"{key}: {value}")


        self.classy_sz = classy_sz_agent(llm_config=llm_config)
        self.classy = classy_agent(llm_config=llm_config)
        self.camb = camb_agent(llm_config=llm_config)
        self.cobaya = cobaya_agent(llm_config=llm_config)
        self.getdist = getdist_agent(llm_config=llm_config)
        
        self.act = act_agent(llm_config=llm_config)
        self.planck = planck_agent(llm_config=llm_config)


        self.engineer = engineer_agent(llm_config=llm_config)
        self.planner = planner_agent(llm_config=llm_config)
        self.executor = executor_agent(llm_config=llm_config) 


        # the administrator (us humans)
        self.admin = autogen.UserProxyAgent(
            name="admin",
            system_message="""A human admin. 
            Interact with the planner to discuss the plan. Plan execution needs to be approved by this admin.
            You dont' perform any task other than approving, calling next agent, or requesting a modification to the plan. 
            """,
            code_execution_config=False,
        )



        allowed_transitions = {
            self.admin: [self.executor.agent, 
                    self.planner.agent, 
                    self.planck.agent, 
                    self.act.agent, 
                    self.getdist.agent, 
                    self.classy_sz.agent, 
                    self.engineer.agent, 
                    self.cobaya.agent, 
                    self.classy.agent],

            self.planner.agent: [self.admin],
            self.engineer.agent: [self.admin],
            self.executor.agent: [self.admin],

            self.planck.agent: [self.admin], 
            self.act.agent: [self.admin],
            self.getdist.agent: [self.admin,self.engineer.agent],
            self.cobaya.agent:  [self.admin,self.engineer.agent],
            self.classy_sz.agent: [self.admin,self.engineer.agent],
            self.classy.agent: [self.admin,self.engineer.agent],
        }


        self.groupchat = autogen.GroupChat(
            
            agents=[self.admin, 
                    self.planner.agent,
                    self.engineer.agent, 
                    self.classy_sz.agent, 
                    self.classy.agent, 
                    self.cobaya.agent, 
                    self.planck.agent,
                    self.getdist.agent,
                    self.act.agent, 
                    self.executor.agent], 

            allowed_or_disallowed_speaker_transitions=allowed_transitions,
            speaker_transitions_type="allowed",
            messages=[], 
            speaker_selection_method = "auto",
            max_round=5
        )

        self.manager = autogen.GroupChatManager(groupchat=self.groupchat, 
                                                llm_config=llm_config)
        
        for agent in self.groupchat.agents:
            agent.reset()   

    def solve(self, task = None):
        self.session = self.admin.initiate_chat(self.manager, message = task)


    def restore_session(self):
        previous_state = f"{self.groupchat.messages}"
        # Convert string to Python dictionary
        dict_representation = ast.literal_eval(previous_state)
        # Convert dictionary to JSON string
        json_string = json.dumps(dict_representation)

        # Prepare the group chat for resuming
        last_agent, last_message = self.manager.resume(messages=json_string)

        # Resume the chat using the last agent and message
        self.session = last_agent.initiate_chat(recipient=self.manager, 
                                          message=last_message, 
                                          clear_history=False)

        
    

    def hello_cmbagent(self):
        return "Hello from cmbagent!"





