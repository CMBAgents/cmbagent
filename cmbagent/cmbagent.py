
import sys

from .utils import *

from .assistants.classy_sz import ClassySzAgent
from .assistants.classy import ClassyAgent
from .assistants.camb import CambAgent
from .assistants.cobaya import CobayaAgent
from .assistants.getdist import GetdistAgent

from .assistants.act import ActAgent
from .assistants.planck import PlanckAgent

from cmbagent.engineer.engineer import EngineerAgent
from cmbagent.planner.planner import PlannerAgent
from cmbagent.executor.executor import ExecutorAgent

from cmbagent.admin.admin import AdminAgent





logger = logging.getLogger(__name__)

class CMBAgent:

    def __init__(self, 
                 cache_seed=42, 
                 temperature=0,
                 timeout=1200,
                 max_round=50,
                 gpt4o_api_key=None, ### llm_api_kay 
                 make_vector_stores=False, #set to True to update all vector_stores, or a list of agents to update only those vector_stores
                 **kwargs):
        """
        Initialize the CMBAgent.

        Args:
            cache_seed (int, optional): Seed for caching. Defaults to 42.
            temperature (float, optional): Temperature for LLM sampling. Defaults to 0.
            timeout (int, optional): Timeout for LLM requests in seconds. Defaults to 1200.
            max_round (int, optional): Maximum number of conversation rounds. Defaults to 50. If too small, the conversation stops. 
            gpt4o_api_key (str, optional): API key for GPT-4. If None, uses the key from the config file.
            make_vector_stores (bool, optional): Whether to create vector stores. Defaults to False.
            **kwargs: Additional keyword arguments.

        Attributes:
            kwargs (dict): Additional keyword arguments.
            work_dir (str): Working directory for output.
            path_to_assistants (str): Path to the assistants directory.
            oai_api_key (str): OpenAI API key.
            classy_sz (classy_sz_agent): Agent for Class_sz operations.
            classy (classy_agent): Agent for Class operations.
            camb (camb_agent): Agent for CAMB operations.
            cobaya (cobaya_agent): Agent for Cobaya operations.
            getdist (getdist_agent): Agent for GetDist operations.
            act (act_agent): Agent for ACT operations.
            planck (planck_agent): Agent for Planck operations.
            engineer (engineer_agent): Agent for engineering tasks.
            planner (planner_agent): Agent for planning tasks.
            executor (executor_agent): Agent for executing tasks.

        Note:
            This class initializes various agents and configurations for cosmological data analysis.
        """
        
        self.kwargs = kwargs

        self.work_dir = work_dir

        self.path_to_assistants = path_to_assistants
        
        logger.info(f"Autogen version: {autogen.__version__}")


        gpt4o_config = config_list_from_json(f"{path_to_apis}/oai_gpt4o.json") ### pass as parameter 

        if gpt4o_api_key is not None:
        
            gpt4o_config[0]['api_key'] = gpt4o_api_key

        self.oai_api_key = gpt4o_config[0]['api_key']


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


        ### instantiate agents -- can be probably done in one line 
        ### remove instances here unless needed. 

        ### RAG agents, they are user specific. 

        self.classy_sz = ClassySzAgent(llm_config=llm_config)
        self.classy = ClassyAgent(llm_config=llm_config)
        self.camb = CambAgent(llm_config=llm_config)
        self.cobaya = CobayaAgent(llm_config=llm_config)
        self.getdist = GetdistAgent(llm_config=llm_config)
        
        self.act = ActAgent(llm_config=llm_config)
        self.planck = PlanckAgent(llm_config=llm_config)


        ### by default are always here 
        self.engineer = EngineerAgent(llm_config=llm_config)
        self.planner = PlannerAgent(llm_config=llm_config)
        self.executor = ExecutorAgent(llm_config=llm_config) 


        # the administrator (us humans)
        self.admin = AdminAgent()


        # all agents 
        self.agents = [self.admin, 
                       self.planner, 
                       self.engineer, 
                       self.executor, 
                       self.act, 
                       self.planck, 
                       self.getdist, 
                       self.cobaya, 
                       self.camb, 
                       self.classy, 
                       self.classy_sz]
        

        ## here we should ask if we need to update vector stores 
        if make_vector_stores != False:

            self.push_vector_stores(make_vector_stores)


        # then we set the agents
        for agent in self.agents:

            agent.set_agent()


        self.allowed_transitions = self.get_allowed_transitions()



        self.groupchat = autogen.GroupChat(
                                agents=[agent.agent for agent in self.agents], 
                                allowed_or_disallowed_speaker_transitions=self.allowed_transitions,
                                speaker_transitions_type="allowed",
                                messages=[], 
                                speaker_selection_method = "auto",
                                max_round=max_round)

        self.manager = autogen.GroupChatManager(groupchat=self.groupchat, 
                                                llm_config=llm_config)
        
        for agent in self.groupchat.agents:

            agent.reset()   

    def solve(self, task = None):

        self.session = self.admin.agent.initiate_chat(self.manager,message = task)
        


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



    def push_vector_stores(self, make_vector_stores):

        client = OpenAI(api_key = self.oai_api_key) 

        # 1. identify rag agents and set store names 

        store_names = []
        rag_agents = []

        for agent in self.agents:


            if type(make_vector_stores) == list and agent.info['name'] not in make_vector_stores and agent.info['name'].replace('_agent', '') not in make_vector_stores:
                continue
            
            if 'assistant_config' in agent.info:

                if 'file_search' in agent.info['assistant_config']['tool_resources'][0].keys():
                    
                    print(agent.info['name'])
                    
                    print(agent.info['assistant_config']['assistant_id'])
                    
                    print(agent.info['assistant_config']['tool_resources'][0]['file_search'][0])
                    
                    store_names.append(f"{agent.info['name']}_store")
                    
                    rag_agents.append(agent)
        

        # 2. collect all vector stores 

        # Set the headers for authentication
        headers = {
            "Authorization": f"Bearer {self.oai_api_key}",
            "OpenAI-Beta": "assistants=v2"
        }

        # Define the URL endpoint for listing vector stores
        url = "https://api.openai.com/v1/vector_stores"

        # Send a GET request to list vector stores
        response = requests.get(url, headers=headers)

        # Check if the request was successful
        if response.status_code == 200:
            
            vector_stores = response.json()

        else:
            
            print("Failed to retrieve vector stores:", response.status_code, response.text)


        # 3. delete old vector stores if they exist and write new ones

        # Find all vector stores by name and collect their IDs
        for vector_store_name,rag_agent in zip(store_names,rag_agents):
            
            print('dealing with: ',vector_store_name)
            
            matching_vector_store_ids = [
                store['id'] for store in vector_stores['data'] if store['name'] == vector_store_name
            ]

            if matching_vector_store_ids:
                
                print(f"Vector store IDs for '{vector_store_name}':", matching_vector_store_ids)

                for vector_store_id in matching_vector_store_ids:
                
                    # Define the URL endpoint for deleting a vector store by ID
                    delete_url = f"https://api.openai.com/v1/vector_stores/{vector_store_id}"

                    # Send a DELETE request to delete the vector store
                    delete_response = requests.delete(delete_url, headers=headers)

                    # Check if the request was successful
                    if delete_response.status_code == 200:
                    
                        print(f"Vector store with ID '{vector_store_id}' deleted successfully.")
                    
                    else:
                        
                        print("Failed to delete vector store:", delete_response.status_code, delete_response.text)
                        
            else:
                
                print(f"No vector stores found with the name '{vector_store_name}'.")
                
            # Create a vector store called "planck_store"
            vector_store = client.beta.vector_stores.create(name=vector_store_name)
            
            
            # Initialize a list to hold the file paths
            file_paths = []
            
            assistant_data = self.path_to_assistants + '/data/' + vector_store_name.removesuffix('_agent_store')

            # Walk through the directory
            for root, dirs, files in os.walk(assistant_data):
                
                for file in files:
                    
                    print(file)
                    
                    # Get the absolute path of each file
                    file_paths.append(os.path.join(root, file))
                    
            # Ready the files for upload to OpenAI

            file_streams = [open(path, "rb") for path in file_paths]

            # Use the upload and poll SDK helper to upload the files, add them to the vector store,
            # and poll the status of the file batch for completion.
            file_batch = client.beta.vector_stores.file_batches.upload_and_poll(
                    vector_store_id=vector_store.id, 
                    files=file_streams
                    )

            # You can print the status and the file counts of the batch to see the result of this operation.
            print(file_batch.status)
            print(file_batch.file_counts)
            
            rag_agent.info['assistant_config']['tool_resources'][0]['file_search'][0]['vector_store_ids'] = vector_store.id
            
            print('created new vector store with id: ',vector_store.id)
            print('\n')




    def hello_cmbagent(self):

        return "Hello from cmbagent!"





