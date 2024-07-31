
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

from cmbagent.admin.admin import admin_agent



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
        self.admin = admin_agent()

        # all agents 
        self.agents = [self.admin, 
                       self.planner, 
                       self.engineer, 
                       self.executor, 
                       self.act, 
                       self.planck, 
                       self.getdist, 
                       self.cobaya, 
                       self.classy, 
                       self.classy_sz]

        self.allowed_transitions = self.get_allowed_transitions()


        self.groupchat = autogen.GroupChat(
                                agents=[agent.agent for agent in self.agents], 
                                allowed_or_disallowed_speaker_transitions=self.allowed_transitions,
                                speaker_transitions_type="allowed",
                                messages=[], 
                                speaker_selection_method = "auto",
                                max_round=5)

        self.manager = autogen.GroupChatManager(groupchat=self.groupchat, 
                                                llm_config=llm_config)
        
        for agent in self.groupchat.agents:

            agent.reset()   

    def solve(self, task = None):

        self.session = self.admin.agent.initiate_chat(self.manager, 
                                                      message = task)


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

        
    def get_agent_from_name(self,name):

        for agent in self.agents: 
        
            if agent.info['name'] == name:
        
                return agent.agent
        
        print(f"get_agent_from_name: agent {name} not found") 
        
        sys.exit()   

    
    def get_allowed_transitions(self):
        
        allowed_transitions = {}

        for agent in self.agents: 

            transition_list = []
            
            for name in agent.info['allowed_transitions']:
            
                transition_list.append(self.get_agent_from_name(name))
        
            allowed_transitions[agent.agent] = transition_list

        return allowed_transitions
    

    def show_allowed_transitions(self):

        for agent, transitions in self.allowed_transitions.items():

            print(f"{agent.name} -> {', '.join([t.name for t in transitions])}")

    def hello_cmbagent(self):

        return "Hello from cmbagent!"





