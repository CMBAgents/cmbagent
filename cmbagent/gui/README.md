# CMBAgent Next.js GUI

This is a modern Next.js version of the CMBAgent GUI, providing the same functionality as the Streamlit version but with improved performance, better UI/UX, and modern web technologies.

## Features

- **Four Operation Modes**: One Shot, Planning and Control, Idea Generation, and Human-in-the-loop
- **Agent Configuration**: Select different LLM models for Engineer and Researcher agents
- **Parameter Tuning**: Adjustable parameters for each mode
- **Real-time Chat Interface**: Interactive conversation with AI agents
- **Responsive Design**: Works on desktop and mobile devices
- **Modern UI**: Built with Tailwind CSS and Lucide React icons

## Prerequisites

- Node.js 18+ 
- npm or yarn package manager
- CMBAgent backend running (for full functionality)

## Installation

1. **Navigate to the GUI directory**:
   ```bash
   cd cmbagent/gui
   ```

2. **Install dependencies**:
   ```bash
   npm install
   # or
   yarn install
   ```

3. **Set up environment variables** (optional):
   Create a `.env.local` file in the `gui` directory:
   ```bash
   # API endpoints (adjust if your backend runs on different ports)
   NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
   ```

## Launch Instructions

### Development Mode

1. **Start the development server**:
   ```bash
   npm run dev
   # or
   yarn dev
   ```

2. **Open your browser** and navigate to:
   ```
   http://localhost:3000
   ```

3. **The app will automatically reload** when you make changes to the code.

### Production Build

1. **Build the application**:
   ```bash
   npm run build
   # or
   yarn build
   ```

2. **Start the production server**:
   ```bash
   npm start
   # or
   yarn start
   ```

3. **Open your browser** and navigate to:
   ```
   http://localhost:3000
   ```

## Usage

### Mode Selection
- **One Shot**: Quick, single-pass responses for immediate tasks
- **Planning and Control**: Multi-step planning with iterative review
- **Idea Generation**: Research project idea generation and refinement
- **Human-in-the-loop**: Context-aware conversations with memory

### Configuration
1. **Select Mode**: Choose your desired operation mode from the sidebar
2. **Configure Agents**: Select LLM models for Engineer and Researcher agents
3. **Set API Keys**: Configure your OpenAI, Anthropic, or other API keys
4. **Adjust Parameters**: Fine-tune mode-specific parameters using the sliders and text areas

### Chat Interface
1. **Type your task** in the input field
2. **Select an agent** (for One Shot and Human-in-the-loop modes)
3. **Send your message** and wait for the AI response
4. **View reasoning** by clicking "Show reasoning" in responses
5. **Use example prompts** to get started quickly

## Project Structure

```
cmbagent/gui/
├── app/                          # Next.js app directory
│   ├── globals.css              # Global styles and Tailwind imports
│   ├── layout.tsx               # Root layout component
│   ├── page.tsx                 # Home page (mode selection)
│   ├── one-shot/                # One Shot mode page
│   ├── planning-and-control/     # Planning and Control mode page
│   ├── idea-generation/          # Idea Generation mode page
│   └── human-in-the-loop/       # Human-in-the-loop mode page
├── components/                   # Reusable React components
│   ├── Sidebar.tsx              # Sidebar with mode selection and config
│   ├── ParametersPanel.tsx      # Mode-specific parameter controls
│   └── ChatInterface.tsx        # Chat interface component
├── package.json                  # Dependencies and scripts
├── tailwind.config.js           # Tailwind CSS configuration
├── tsconfig.json                # TypeScript configuration
└── README.md                    # This file
```

## Customization

### Styling
- Modify `tailwind.config.js` to customize colors, fonts, and spacing
- Edit `app/globals.css` for custom CSS classes and global styles
- Update component-specific styles in individual component files

### Adding New Modes
1. Create a new page in the `app/` directory
2. Add mode configuration to the `Sidebar.tsx` component
3. Update the `ParametersPanel.tsx` component with new parameters
4. Add routing logic in the main page

### Backend Integration
To connect with the actual CMBAgent backend:
1. Update the API endpoints in `next.config.js`
2. Modify the `handleSendMessage` functions in each mode page
3. Replace simulated responses with actual API calls
4. Handle real-time streaming if supported by your backend

## Troubleshooting

### Common Issues

1. **Port already in use**:
   ```bash
   # Kill the process using port 3000
   lsof -ti:3000 | xargs kill -9
   # Or use a different port
   npm run dev -- -p 3001
   ```

2. **Build errors**:
   ```bash
   # Clear Next.js cache
   rm -rf .next
   npm run build
   ```

3. **Dependency issues**:
   ```bash
   # Clear node_modules and reinstall
   rm -rf node_modules package-lock.json
   npm install
   ```

### Performance Optimization

1. **Enable production mode** for better performance:
   ```bash
   npm run build && npm start
   ```

2. **Use dynamic imports** for large components:
   ```typescript
   const DynamicComponent = dynamic(() => import('./Component'), {
     loading: () => <p>Loading...</p>
   })
   ```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project follows the same license as the main CMBAgent project.

## Support

For issues and questions:
- Check the main CMBAgent documentation
- Open an issue on the GitHub repository
- Review the troubleshooting section above
