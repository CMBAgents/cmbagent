from cmbagent.utils import * ### dont use that 


logger = logging.getLogger(__name__) ### fixx here set to agent name 




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

        self.agent = GPTAssistantAgent(
            name= self.info["name"],
            instructions= self.info["instructions"],
            description=self.info["description"],
            assistant_config=self.info["assistant_config"],
            llm_config=self.llm_config,
        )



