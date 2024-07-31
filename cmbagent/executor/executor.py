from cmbagent.utils import *

logger = logging.getLogger(__name__)

class executor_agent(object):
    def __init__(self, 
                 llm_config=None,
                 **kwargs):
        
        self.kwargs = kwargs

        input_file = os.path.join(path_to_executor, "executor.yaml")
        self.info = yaml_load_file(input_file)
        
        logger.info("Loaded assistant info:")

        for key, value in self.info.items():
            logger.info(f"{key}: {value}")


        self.agent = UserProxyAgent(
            name= self.info["name"],
            system_message= self.info["instructions"],
            description=self.info["description"],
            llm_config=llm_config,
            human_input_mode=self.info["human_input_mode"],
            max_consecutive_auto_reply=self.info["max_consecutive_auto_reply"],
            is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
            code_execution_config={
                "executor": LocalCommandLineCodeExecutor(work_dir=work_dir,
                                                         timeout = self.info["timeout"]),
            },
        )





