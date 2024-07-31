from cmbagent.utils import *

logger = logging.getLogger(__name__)

class classy_agent(object):
    def __init__(self, 
                 llm_config=None,
                 **kwargs):
        
        self.kwargs = kwargs

        input_file = os.path.join(path_to_assistants, "classy.yaml")
        self.info = yaml_load_file(input_file)
        
        logger.info("Loaded assistant info:")

        for key, value in self.info.items():
            logger.info(f"{key}: {value}")


        self.agent = GPTAssistantAgent(
            name= self.info["name"],
            instructions= self.info["instructions"],
            description=self.info["description"],
            assistant_config=self.info["assistant_config"],
            llm_config=llm_config,
        )


