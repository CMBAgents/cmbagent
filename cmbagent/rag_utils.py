import os
import importlib
from .utils import path_to_assistants,OpenAI,default_chunking_strategy,default_top_p,default_temperature,default_select_speaker_prompt_template,default_select_speaker_message_template, YAML
from autogen.cmbagent_utils import cmbagent_debug
from .utils import update_yaml_preserving_format
import requests, pprint

def import_rag_agents():        
    imported_rag_agents = {}
    for filename in os.listdir(path_to_assistants):
        if filename.endswith(".py") and filename != "__init__.py" and filename[0] != ".":
            module_name = filename[:-3]  # Remove the .py extension
            class_name = ''.join([part.capitalize() for part in module_name.split('_')]) + 'Agent'
            module_path = f"cmbagent.agents.rag_agents.{module_name}"
            module = importlib.import_module(module_path)
            agent_class = getattr(module, class_name)
            imported_rag_agents[class_name] = {}
            imported_rag_agents[class_name]['agent_class'] = agent_class
            imported_rag_agents[class_name]['agent_name'] = module_name
    return imported_rag_agents





def push_vector_stores(cmbagent_instance, make_vector_stores, chunking_strategy, verbose = False):

    if make_vector_stores == False:
        return

    client = OpenAI(api_key = cmbagent_instance.llm_api_key)

    # 1. identify rag agents and set store names

    store_names = []
    rag_agents = []

    for agent in cmbagent_instance.agents:


        if type(make_vector_stores) == list and agent.info['name'] not in make_vector_stores and agent.info['name'].replace('_agent', '') not in make_vector_stores:
            continue

        if 'assistant_config' in agent.info:

            if 'file_search' in agent.info['assistant_config']['tool_resources'].keys():
                if cmbagent_debug:
                    print(f"Updating vector store for {agent.info['name']}")

                # print(agent.info['assistant_config']['assistant_id'])

                # print(agent.info['assistant_config']['tool_resources']['file_search'])

                store_names.append(f"{agent.info['name']}_store")

                rag_agents.append(agent)


    # 2. collect all vector stores

    # Set the headers for authentication
    headers = {
        "Authorization": f"Bearer {cmbagent_instance.llm_api_key}",
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
    vector_store_ids = {}
    for vector_store_name,rag_agent in zip(store_names,rag_agents):

        # print('dealing with: ',vector_store_name)

        matching_vector_store_ids = [
            store['id'] for store in vector_stores['data'] if store['name'] == vector_store_name
        ]

        if matching_vector_store_ids:

            # print(f"Vector store IDs for '{vector_store_name}':", matching_vector_store_ids)

            for vector_store_id in matching_vector_store_ids:

                # Define the URL endpoint for deleting a vector store by ID
                delete_url = f"https://api.openai.com/v1/vector_stores/{vector_store_id}"

                # Send a DELETE request to delete the vector store
                delete_response = requests.delete(delete_url, headers=headers)

                # Check if the request was successful
                if delete_response.status_code == 200:

                    # print(f"Vector store with ID '{vector_store_id}' deleted successfully.")

                    continue

                else:

                    print("Failed to delete vector store:", delete_response.status_code, delete_response.text)

        else:

            if cmbagent_debug:
                print(f"No vector stores found with the name '{vector_store_name}'.")

        # print()

        # print(rag_agent.name)
        chunking_strategy = chunking_strategy[rag_agent.name] if chunking_strategy and rag_agent.name in chunking_strategy else default_chunking_strategy
        if verbose or cmbagent_debug:
            print(f"{rag_agent.name}: chunking strategy: ")
            pprint.pprint(chunking_strategy)
        # print()

        # print('calling client.beta.vector_stores.create')
        # Create a vector store called "planck_store"
        vector_store = client.vector_stores.create(name=vector_store_name,
                                                        chunking_strategy=chunking_strategy)

        # print('created vector store with id: ',vector_store.id)
        # print('\n')

        # Initialize a list to hold the file paths
        file_paths = []

        assistant_data = os.getenv('CMBAGENT_DATA') + "/data/" + vector_store_name.removesuffix('_agent_store')


        if cmbagent_debug:
            print("Files to upload:")
        for root, dirs, files in os.walk(assistant_data):
            # Filter out unwanted directories like .ipynb_checkpoints
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for file in files:

                if file.startswith('.') or file.endswith('.ipynb')  or file.endswith('.yaml') or file.endswith('.txt'):

                    continue

                if cmbagent_debug:
                    print(f"\t - {file}")

                # Get the absolute path of each file
                file_paths.append(os.path.join(root, file))

        # Ready the files for upload to OpenAI

        file_streams = [open(path, "rb") for path in file_paths]

        # Use the upload and poll SDK helper to upload the files, add them to the vector store,
        # and poll the status of the file batch for completion.
        file_batch = client.vector_stores.file_batches.upload_and_poll(
                vector_store_id=vector_store.id,
                files=file_streams
                )

        # You can print the status and the file counts of the batch to see the result of this operation.
        if cmbagent_debug:
            print(file_batch.status)
            print(file_batch.file_counts)

        rag_agent.info['assistant_config']['tool_resources']['file_search']['vector_store_ids'] = [vector_store.id]

        if cmbagent_debug:
            print(f'{rag_agent.name}: uploaded assistant data to vector store with id: ',vector_store.id)
            print('\n')
        new_vector_store_ids = {rag_agent.name : vector_store.id}
        vector_store_ids.update(new_vector_store_ids)

    cmbagent_instance.vector_store_ids = vector_store_ids

    if cmbagent_debug:
        print("vector stores updated")

        for key, value in cmbagent_instance.vector_store_ids.items():
            print(f"'{key}': '{value}',")

    # vector_store_ids = self.vector_store_ids

    for agent_name, vector_id in cmbagent_instance.vector_store_ids.items():
        update_yaml_preserving_format(f"{path_to_assistants}/{agent_name.replace('_agent', '') }.yaml", agent_name, vector_id)





def make_rag_agents(make_new_rag_agents):
    """
    Create new RAG agents based on the provided list of agent names.

    This function generates Python and YAML files for each new agent specified
    in the 'make_new_rag_agents' list. It creates:
    1. A Python file with a basic agent class structure.
    2. A YAML file with initial configuration for the agent.
    3. A data folder for each agent to store relevant files.

    Args:
        make_new_rag_agents (list): A list of strings, where each string is the
                                    name of a new agent to be created.

    Returns:
        dict: A dictionary where keys are agent names and values are paths to
              their respective data folders.

    Note:
    - The Python file will contain a class definition inheriting from BaseAgent.
    - The YAML file will include basic configuration like name, instructions,
      and tool definitions.
    - Existing files with the same names will be overwritten.
    - A new data folder is created for each agent in the assistants directory.
    """
    data_folders = {}
    for agent_name in make_new_rag_agents:
        # Create the Python file for the agent
        agent_file_path = os.path.join(path_to_assistants, f"{agent_name}.py")
        with open(agent_file_path, "w") as f:
            f.write(f"""import os
from cmbagent.base_agent import BaseAgent


class {agent_name.capitalize()}Agent(BaseAgent):

    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)
""")

        # Create the YAML file for the agent
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.indent(mapping=2, sequence=4, offset=2)
        
        yaml_file_path = os.path.join(path_to_assistants, f"{agent_name}.yaml")

        yaml_content = {
            "name": f"{agent_name}_agent",
            "instructions": f"You are the {agent_name}_agent in the team. Your role is to assist with tasks related to {agent_name}.",
            "assistant_config": {
                "assistant_id": "asst_ijk",
                "tools": [
                    {
                        "type": "file_search"
                    }
                ],
                "tool_resources": {
                    "file_search": {
                        "vector_store_ids": [
                            "vs_xyz"
                        ]
                    }
                }
            },
            "description": f"This is the {agent_name}_agent: a retrieval agent that provides assistance with {agent_name.upper()}. It must perform retrieval augmented generation and include the <filenames> in the response.",
            "allowed_transitions": [
                "admin"
            ]
        }
        
        with open(yaml_file_path, "w") as f:
            yaml.dump(yaml_content, f)

        print(f"Created {agent_name} agent files: {agent_file_path} and {yaml_file_path}")
        # Create a folder for the agent's data
        # agent_data_folder = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'data', agent_name)
        dir_path = os.getenv('CMBAGENT_DATA')
        agent_data_folder = os.path.join(dir_path, 'data', agent_name)
        print(f"Creating data folder for {agent_name} agent: {agent_data_folder}")
        os.makedirs(agent_data_folder, exist_ok=True)
        print(f"Created data folder for {agent_name} agent: {agent_data_folder}")
        print(f"Please deposit any relevant files for the {agent_name} agent in this folder.")

    # Return a dictionary with the full paths to the agent data folders
    data_folders = {}
    # data_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'data')
    data_dir = os.path.join(dir_path, 'data')
    for agent_folder in os.listdir(data_dir):
        full_path = os.path.join(data_dir, agent_folder)
        if os.path.isdir(full_path):
            data_folders[agent_folder] = full_path
    return data_folders

