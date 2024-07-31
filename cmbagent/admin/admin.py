from cmbagent.utils import *

logger = logging.getLogger(__name__)

class admin_agent(object):

    def __init__(self, 
                 **kwargs):
        
        self.kwargs = kwargs

        input_file = os.path.join(path_to_admin, "admin.yaml")
        
        self.info = yaml_load_file(input_file)
        
        logger.info("Loaded assistant info:")

        for key, value in self.info.items():
            logger.info(f"{key}: {value}")



    def set_agent(self):

        self.agent = autogen.UserProxyAgent(
            name= self.info["name"],
            system_message= self.info["instructions"],
            code_execution_config=self.info["code_execution_config"],
        )





