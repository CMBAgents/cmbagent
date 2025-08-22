'use client'

interface ParametersPanelProps {
  currentMode: string
  parameters: Record<string, any>
  onParameterChange: (key: string, value: any) => void
}

export default function ParametersPanel({
  currentMode,
  parameters,
  onParameterChange
}: ParametersPanelProps) {
  const renderOneShotParams = () => (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Max Rounds
        </label>
        <input
          type="range"
          min="1"
          max="50"
          value={parameters.max_rounds || 25}
          onChange={(e) => onParameterChange('max_rounds', parseInt(e.target.value))}
          className="w-full"
        />
        <div className="text-center text-sm text-gray-600 mt-1">
          {parameters.max_rounds || 25}
        </div>
      </div>
      
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Max Attempts
        </label>
        <input
          type="range"
          min="1"
          max="10"
          value={parameters.max_n_attempts || 6}
          onChange={(e) => onParameterChange('max_n_attempts', parseInt(e.target.value))}
          className="w-full"
        />
        <div className="text-center text-sm text-gray-600 mt-1">
          {parameters.max_n_attempts || 6}
        </div>
      </div>
    </div>
  )

  const renderPlanningControlParams = () => (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Max Rounds
        </label>
        <input
          type="range"
          min="1"
          max="1000"
          value={parameters.max_rounds_control || 500}
          onChange={(e) => onParameterChange('max_rounds_control', parseInt(e.target.value))}
          className="w-full"
        />
        <div className="text-center text-sm text-gray-600 mt-1">
          {parameters.max_rounds_control || 500}
        </div>
      </div>
      
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Max Steps
        </label>
        <input
          type="range"
          min="1"
          max="10"
          value={parameters.max_plan_steps || 4}
          onChange={(e) => onParameterChange('max_plan_steps', parseInt(e.target.value))}
          className="w-full"
        />
        <div className="text-center text-sm text-gray-600 mt-1">
          {parameters.max_plan_steps || 4}
        </div>
      </div>
    </div>
  )

  const renderIdeaGenerationParams = () => (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Max Rounds
        </label>
        <input
          type="range"
          min="1"
          max="1000"
          value={parameters.max_rounds_control || 500}
          onChange={(e) => onParameterChange('max_rounds_control', parseInt(e.target.value))}
          className="w-full"
        />
        <div className="text-center text-sm text-gray-600 mt-1">
          {parameters.max_rounds_control || 500}
        </div>
      </div>
      
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Max Steps
        </label>
        <input
          type="range"
          min="1"
          max="10"
          value={parameters.max_plan_steps || 6}
          onChange={(e) => onParameterChange('max_plan_steps', parseInt(e.target.value))}
          className="w-full"
        />
        <div className="text-center text-sm text-gray-600 mt-1">
          {parameters.max_plan_steps || 6}
        </div>
      </div>
    </div>
  )

  const renderHumanInTheLoopParams = () => (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Max Rounds
        </label>
        <input
          type="range"
          min="1"
          max="100"
          value={parameters.max_rounds || 50}
          onChange={(e) => onParameterChange('max_rounds', parseInt(e.target.value))}
          className="w-full"
        />
        <div className="text-center text-sm text-gray-600 mt-1">
          {parameters.max_rounds || 50}
        </div>
      </div>
      
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Max Attempts
        </label>
        <input
          type="range"
          min="1"
          max="10"
          value={parameters.max_n_attempts || 6}
          onChange={(e) => onParameterChange('max_n_attempts', parseInt(e.target.value))}
          className="w-full"
        />
        <div className="text-center text-sm text-gray-600 mt-1">
          {parameters.max_n_attempts || 6}
        </div>
      </div>
    </div>
  )

  const renderParameters = () => {
    switch (currentMode) {
      case 'one_shot':
        return renderOneShotParams()
      case 'planning_and_control':
        return renderPlanningControlParams()
      case 'idea_generation':
        return renderIdeaGenerationParams()
      case 'human_in_the_loop':
        return renderHumanInTheLoopParams()
      default:
        return null
    }
  }

  return (
    <div className="bg-white border-b border-gray-200 p-4">
      <div className="max-w-4xl mx-auto">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Parameters</h2>
        {renderParameters()}
      </div>
    </div>
  )
}
