from cmbagent.utils import *

logger = logging.getLogger(__name__)

class planner_agent(object):
    def __init__(self, 
                 llm_config=None,
                 **kwargs):
        
        self.kwargs = kwargs

        input_file = os.path.join(path_to_planner, "planner.yaml")
        self.info = yaml_load_file(input_file)
        
        logger.info("Loaded assistant info:")

        for key, value in self.info.items():
            logger.info(f"{key}: {value}")


        self.agent = AssistantAgent(
            name= self.info["name"],
            system_message= self.info["instructions"],
            description=self.info["description"],
            llm_config=llm_config,
        )







