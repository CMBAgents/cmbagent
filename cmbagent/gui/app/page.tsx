'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Zap, Settings, Lightbulb, Users } from 'lucide-react'

export default function Home() {
  const modes = [
    {
      id: 'one_shot',
      title: 'One Shot',
      description: 'Quick single responses',
      icon: Zap,
      href: '/one-shot',
      color: 'bg-blue-500'
    },
    {
      id: 'planning_and_control',
      title: 'Planning & Control',
      description: 'Multi-step analysis',
      icon: Settings,
      href: '/planning-and-control',
      color: 'bg-green-500'
    },
    {
      id: 'idea_generation',
      title: 'Idea Generation',
      description: 'Research project ideas',
      icon: Lightbulb,
      href: '/idea-generation',
      color: 'bg-yellow-500'
    },
    {
      id: 'human_in_the_loop',
      title: 'Human in the Loop',
      description: 'Conversational AI',
      icon: Users,
      href: '/human-in-the-loop',
      color: 'bg-purple-500'
    }
  ]

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Simple Header */}
      <div className="text-center py-16 px-4">
        <h1 className="text-6xl font-bold text-gray-900 mb-4">
          CMBAGENT
        </h1>
        <p className="text-xl text-gray-600 mb-8">
          Multi-agent system for Data Driven Discovery
        </p>
        <div className="text-sm text-gray-500">
          Powered by AG2
        </div>
      </div>

      {/* Simple Mode Grid */}
      <div className="max-w-4xl mx-auto px-4 pb-16">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {modes.map((mode) => (
            <Link key={mode.id} href={mode.href}>
              <div className="bg-white rounded-lg p-8 shadow-sm hover:shadow-md transition-shadow cursor-pointer border border-gray-200">
                <div className={`${mode.color} w-16 h-16 rounded-lg flex items-center justify-center mx-auto mb-4`}>
                  <mode.icon size={32} className="text-white" />
                </div>
                <h3 className="text-2xl font-semibold text-gray-900 text-center mb-2">
                  {mode.title}
                </h3>
                <p className="text-gray-600 text-center">
                  {mode.description}
                </p>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}
