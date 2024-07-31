
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







    def hello_cmbagent(self):
        return "Hello from cmbagent!"





