#!/usr/bin/env python3
"""
Simple Flask server to expose CMBAgent functions as API endpoints.
This replicates exactly how the functions are called in gui.py
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import sys
import traceback
import time

# Since cmbagent is installed via pip, we can import directly
try:
    from cmbagent.cmbagent import one_shot, planning_and_control_context_carryover
    from cmbagent.utils import get_api_keys_from_env
    print("✅ Successfully imported CMBAgent functions from installed package")
except ImportError as e:
    print(f"❌ Failed to import CMBAgent: {e}")
    print("❌ Make sure cmbagent is installed: pip install .")
    print("❌ Or check if you're in the right virtual environment")
    sys.exit(1)

app = Flask(__name__)
CORS(app)  # Enable CORS for Next.js frontend

@app.route('/api/one_shot', methods=['POST'])
def api_one_shot():
    """Expose the one_shot function exactly as called in gui.py"""
    try:
        data = request.get_json()
        
        # Extract parameters exactly as in gui.py
        user_input = data.get('user_input')
        max_rounds = data.get('max_rounds', 25)
        max_n_attempts = data.get('max_n_attempts', 6)
        engineer_model = data.get('engineer_model', 'gpt-4o')
        researcher_model = data.get('researcher_model', 'gpt-4o')
        agent = data.get('agent', 'engineer')
        api_keys = data.get('api_keys', {})
        work_dir = data.get('work_dir', 'project_dir')
        
        print(f"🚀 Calling one_shot with:")
        print(f"  - user_input: {user_input}")
        print(f"  - max_rounds: {max_rounds}")
        print(f"  - max_n_attempts: {max_n_attempts}")
        print(f"  - engineer_model: {engineer_model}")
        print(f"  - researcher_model: {researcher_model}")
        print(f"  - agent: {agent}")
        print(f"  - work_dir: {work_dir}")
        print(f"  - api_keys: {list(api_keys.keys()) if api_keys else 'None'}")
        
        # Handle API keys exactly as in gui.py
        # The CMBAgent functions expect api_keys dict, not environment variables
        if not api_keys:
            # Fallback to environment variables if no keys provided
            api_keys = get_api_keys_from_env()
            print(f"  - Using environment API keys: {list(api_keys.keys()) if api_keys else 'None'}")
        
        # Call the function exactly as in gui.py
        start_time = time.time()
        results = one_shot(
            user_input,
            max_rounds=max_rounds,
            max_n_attempts=max_n_attempts,
            engineer_model=engineer_model,
            researcher_model=researcher_model,
            agent=agent,
            api_keys=api_keys,  # Pass api_keys dict directly
            work_dir=work_dir,
        )
        elapsed = time.time() - start_time
        
        print(f"✅ one_shot completed in {elapsed:.2f}s")
        print(f"📊 Results keys: {list(results.keys()) if isinstance(results, dict) else 'Not a dict'}")
        
        # Return the results exactly as they come from the function
        return jsonify(results)
        
    except Exception as e:
        print(f"❌ Error in one_shot API: {e}")
        print(traceback.format_exc())
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc(),
            'chat_history': []
        }), 500

@app.route('/api/planning_and_control', methods=['POST'])
def api_planning_and_control():
    """Expose the planning_and_control_context_carryover function exactly as called in gui.py"""
    try:
        data = request.get_json()
        
        # Extract parameters exactly as in gui.py
        user_input = data.get('user_input')
        max_rounds_control = data.get('max_rounds_control', 500)
        n_plan_reviews = data.get('n_plan_reviews', 1)
        max_n_attempts = data.get('max_n_attempts', 6)
        max_plan_steps = data.get('max_plan_steps', 4)
        plan_instructions = data.get('plan_instructions', '')
        hardware_constraints = data.get('hardware_constraints', '')
        engineer_model = data.get('engineer_model', 'gpt-4o')
        researcher_model = data.get('researcher_model', 'gpt-4o')
        api_keys = data.get('api_keys', {})
        work_dir = data.get('work_dir', 'project_dir')
        
        print(f"🚀 Calling planning_and_control_context_carryover with:")
        print(f"  - user_input: {user_input}")
        print(f"  - max_rounds_control: {max_rounds_control}")
        print(f"  - n_plan_reviews: {n_plan_reviews}")
        print(f"  - max_n_attempts: {max_n_attempts}")
        print(f"  - max_plan_steps: {max_plan_steps}")
        print(f"  - plan_instructions: {plan_instructions[:100]}...")
        print(f"  - hardware_constraints: {hardware_constraints[:100]}...")
        print(f"  - engineer_model: {engineer_model}")
        print(f"  - researcher_model: {researcher_model}")
        print(f"  - work_dir: {work_dir}")
        print(f"  - api_keys: {list(api_keys.keys()) if api_keys else 'None'}")
        
        # Handle API keys exactly as in gui.py
        if not api_keys:
            # Fallback to environment variables if no keys provided
            api_keys = get_api_keys_from_env()
            print(f"  - Using environment API keys: {list(api_keys.keys()) if api_keys else 'None'}")
        
        # Call the function exactly as in gui.py
        start_time = time.time()
        results = planning_and_control_context_carryover(
            user_input,
            max_rounds_control=max_rounds_control,
            n_plan_reviews=n_plan_reviews,
            max_n_attempts=max_n_attempts,
            max_plan_steps=max_plan_steps,
            engineer_model=engineer_model,
            researcher_model=researcher_model,
            plan_instructions=plan_instructions,
            hardware_constraints=hardware_constraints,
            api_keys=api_keys,  # Pass api_keys dict directly
            work_dir=work_dir,
        )
        elapsed = time.time() - start_time
        
        print(f"✅ planning_and_control completed in {elapsed:.2f}s")
        print(f"📊 Results keys: {list(results.keys()) if isinstance(results, dict) else 'Not a dict'}")
        
        # Return the results exactly as they come from the function
        return jsonify(results)
        
    except Exception as e:
        print(f"❌ Error in planning_and_control API: {e}")
        print(traceback.format_exc())
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc(),
            'chat_history': []
        }), 500

@app.route('/api/idea_generation', methods=['POST'])
def api_idea_generation():
    """Expose the idea_generation function (uses planning_and_control_context_carryover)"""
    try:
        data = request.get_json()
        
        # Extract parameters exactly as in gui.py
        user_input = data.get('user_input')
        max_rounds_control = data.get('max_rounds_control', 500)
        n_plan_reviews = data.get('n_plan_reviews', 1)
        max_plan_steps = data.get('max_plan_steps', 6)
        plan_instructions = data.get('plan_instructions', '')
        engineer_model = data.get('engineer_model', 'gpt-4o')
        researcher_model = data.get('researcher_model', 'gpt-4o')
        api_keys = data.get('api_keys', {})
        work_dir = data.get('work_dir', 'project_dir')
        
        print(f"🚀 Calling idea_generation with:")
        print(f"  - user_input: {user_input}")
        print(f"  - max_rounds_control: {max_rounds_control}")
        print(f"  - n_plan_reviews: {n_plan_reviews}")
        print(f"  - max_plan_steps: {max_plan_steps}")
        print(f"  - plan_instructions: {plan_instructions[:100]}...")
        print(f"  - engineer_model: {engineer_model}")
        print(f"  - researcher_model: {researcher_model}")
        print(f"  - work_dir: {work_dir}")
        print(f"  - api_keys: {list(api_keys.keys()) if api_keys else 'None'}")
        
        # Handle API keys exactly as in gui.py
        if not api_keys:
            # Fallback to environment variables if no keys provided
            api_keys = get_api_keys_from_env()
            print(f"  - Using environment API keys: {list(api_keys.keys()) if api_keys else 'None'}")
        
        # Call the function exactly as in gui.py (idea_generation uses planning_and_control)
        start_time = time.time()
        results = planning_and_control_context_carryover(
            user_input,
            max_rounds_control=max_rounds_control,
            n_plan_reviews=n_plan_reviews,
            max_plan_steps=max_plan_steps,
            engineer_model=engineer_model,
            researcher_model=researcher_model,
            plan_instructions=plan_instructions,
            api_keys=api_keys,  # Pass api_keys dict directly
            work_dir=work_dir,
        )
        elapsed = time.time() - start_time
        
        print(f"✅ idea_generation completed in {elapsed:.2f}s")
        print(f"📊 Results keys: {list(results.keys()) if isinstance(results, dict) else 'Not a dict'}")
        
        # Return the results exactly as they come from the function
        return jsonify(results)
        
    except Exception as e:
        print(f"❌ Error in idea_generation API: {e}")
        print(traceback.format_exc())
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc(),
            'chat_history': []
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        # Test if we can get API keys from environment
        env_api_keys = get_api_keys_from_env()
        return jsonify({
            'status': 'healthy',
            'cmbagent_imported': True,
            'environment_api_keys': list(env_api_keys.keys()) if env_api_keys else [],
            'endpoints': [
                '/api/one_shot',
                '/api/planning_and_control', 
                '/api/idea_generation'
            ]
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'cmbagent_imported': True
        }), 500

if __name__ == '__main__':
    print("🚀 Starting CMBAgent Flask Server...")
    print("📍 This server exposes CMBAgent functions as API endpoints")
    print("🌐 Frontend can connect to: http://localhost:8000")
    print("📋 Available endpoints:")
    print("   - POST /api/one_shot")
    print("   - POST /api/planning_and_control")
    print("   - POST /api/idea_generation")
    print("   - GET  /health")
    print()
    
    app.run(host='0.0.0.0', port=8000, debug=True)
