from cmbagent.utils import * ### dont use that 


logger = logging.getLogger(__name__) ### fix here set to agent name 
import os



class BaseAgent:

    def __init__(self, 
                 llm_config=None,
                 agent_id=None,
                 **kwargs):
        

        self.kwargs = kwargs

        self.llm_config = llm_config

        self.info = yaml_load_file(agent_id + ".yaml")
        
        logger.info("Loaded assistant info:")

        for key, value in self.info.items():

            logger.info(f"{key}: {value}")


    def set_agent(self):
    
        name = self.info["name"]
        dir_path = os.path.dirname(os.path.realpath(__file__))
        data_path = os.path.join(dir_path, 'data', name.replace('_agent', ''))
        self.info["instructions"] += f'The files uploaded to this agent are {os.listdir(data_path)}.\n'

        #### scrolls through data and builds descriptions of files that are available?  

        self.agent = GPTAssistantAgent(
            name= self.info["name"],
            instructions= self.info["instructions"],
            description=self.info["description"],
            assistant_config=self.info["assistant_config"],
            llm_config=self.llm_config,
            overwrite_tools=True,
            overwrite_instructions=True
        )



