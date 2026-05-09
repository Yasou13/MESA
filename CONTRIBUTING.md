# Contributing to MESA

Thank you for your interest in contributing to MESA! We welcome contributions from the community to help make MESA the premier High-Security, Zero-Hallucination Cognitive Memory Engine for EMR/HIS.

## Contribution Workflow

Please strictly adhere to the following workflow when contributing to MESA:

1. **Fork the Repository**
   Create a personal fork of the MESA repository on GitHub.

2. **Create a Feature Branch**
   Clone your fork locally and create a new branch for your feature or bug fix.
   ```bash
   git checkout -b feature/your-feature-name
   ```

3. **Make Your Changes**
   Implement your changes, ensuring that you adhere to our coding standards. Do not modify any files inside the `mesa_memory/` or `tests/` directories without thorough justification.

4. **Run the Test Suite (Pytest)**
   All contributions must pass the existing test suite. Run the tests using Pytest to ensure your changes are stable.
   ```bash
   pytest tests/ -v
   ```
   Ensure all tests pass before proceeding.

5. **Submit a Pull Request**
   Push your feature branch to your fork and submit a Pull Request against the main MESA repository. Include a detailed description of your changes and reference any related issues.

## Code of Conduct

Please treat all contributors with respect and professionalism. We maintain a zero-tolerance policy for harassment or abusive behavior.
