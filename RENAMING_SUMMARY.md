# Mode Renaming: planning-control → deep_research

**Date**: 2026-01-24
**Status**: ✅ Complete

## Summary

Successfully renamed the "planning-control" mode to "deep_research" throughout the codebase for better clarity and user-facing naming.

## Files Changed

### Frontend (`cmbagent-ui/components/TaskInput.tsx`)
- **10 occurrences updated**
  - Line 36: Type definition for mode state
  - Line 73: Config initial state type annotation
  - Line 112: Conditional for default plan instructions
  - Line 189-194: Button onClick handler (3 instances)
  - Line 199: Button className conditional
  - Line 441: Advanced configuration header
  - Line 548: Model selection conditional comment
  - Line 857: Max rounds label conditional
  - Line 889: Additional options conditional
  - Line 931: Placeholder text conditional

### Backend (`backend/main.py`)
- **2 occurrences updated**
  - Line 777: Mode check for logging message
  - Line 837: Main mode execution branching

### Documentation (`TEST_RESULTS.md`)
- **Multiple occurrences updated**
  - Mode name references in tables
  - Execution mode descriptions
  - Test results summaries

## Verification

✅ **All instances found and updated**
```bash
grep -r "planning-control" /Users/boris/GitHub/cmbagent
# Result: No files found
```

✅ **Frontend build successful**
```bash
npm run build
# Result: Build completed successfully
```

✅ **Type system consistent**
- All TypeScript type annotations updated
- No type errors in compilation

## Technical Details

### Mode Value Changes
- **Old**: `'planning-control'`
- **New**: `'deep_research'`

### Display Name
- **Consistent**: "Deep Research" (already used in UI button)

### Function Calls
No changes needed to core cmbagent functions:
- Backend still calls `cmbagent.planning_and_control_context_carryover()`
- Only the mode identifier passed from frontend changed

## Impact

### User-Facing
- ✅ More intuitive naming ("Deep Research" vs "Planning Control")
- ✅ Consistent with UI button labels
- ✅ Better reflects the mode's purpose

### Technical
- ✅ No breaking changes to core cmbagent API
- ✅ Maintains backward compatibility (only UI/backend layer affected)
- ✅ All type definitions updated consistently

## Testing

- ✅ Frontend TypeScript compilation: PASSED
- ✅ Frontend production build: PASSED
- ✅ Backend syntax validation: PASSED
- ⏳ End-to-end mode execution: Pending (requires API keys)

## Related Components

### Unchanged (By Design)
The following components were verified and do not reference the old mode name:
- `ConsoleOutput.tsx` - Mode-agnostic console display
- `ResultDisplay.tsx` - Mode detection via file structure, not string matching
- `FileBrowser.tsx` - Independent of execution mode
- All credential components - Independent of execution mode
- Backend `credentials.py` - No mode references
- Core cmbagent package - Uses function name, not mode string

### Configuration Flow
```
User clicks "Deep Research" button
  ↓
Frontend: mode = 'deep_research'
  ↓
WebSocket sends: { mode: 'deep_research', ... }
  ↓
Backend checks: if mode == "deep_research"
  ↓
Backend calls: cmbagent.planning_and_control_context_carryover()
  ↓
Results streamed back via WebSocket
```

## Recommendations

### Future Considerations
1. Consider creating a central constants file for mode values:
   ```typescript
   // constants.ts
   export const MODES = {
     ONE_SHOT: 'one-shot',
     DEEP_RESEARCH: 'deep_research',
     IDEA_GENERATION: 'idea-generation',
     // ...
   } as const;
   ```

2. Add mode validation in backend:
   ```python
   VALID_MODES = ['one-shot', 'deep_research', 'idea-generation', 'ocr', 'arxiv', 'enhance-input']
   if mode not in VALID_MODES:
       raise ValueError(f"Invalid mode: {mode}")
   ```

3. Consider using enum type for stronger type safety:
   ```typescript
   enum ExecutionMode {
     OneShot = 'one-shot',
     DeepResearch = 'deep_research',
     IdeaGeneration = 'idea-generation',
     // ...
   }
   ```
