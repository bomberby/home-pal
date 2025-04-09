
  // Shopping List Functions
  function addShoppingItem() {
      const input = document.getElementById('shopping-input');
      const list = document.getElementById('shopping-list');
      
      if (input.value.trim()) {
          const li = document.createElement('li');
          li.className = 'shopping-item';
          li.innerHTML = `
              <span class="item">${input.value}</span>
              <button class="shopping-button" onclick="deleteShoppingItem(this)">Delete</button>
          `;
          list.appendChild(li);
          
          // Save to localStorage
          const shoppingItems = JSON.parse(localStorage.getItem('shoppingList') || '[]');
          shoppingItems.push(input.value.trim());
          localStorage.setItem('shoppingList', JSON.stringify(shoppingItems));
          
          input.value = '';
      }
  }

  function deleteShoppingItem(button) {
      button.parentElement.remove();
      
      // Remove from localStorage
      const shoppingItems = JSON.parse(localStorage.getItem('shoppingList') || '[]');
      const index = shoppingItems.findIndex(item => 
          item === button.parentElement.getElementsByClassName('item')[0].textContent.trim());
      if (index !== -1) {
          shoppingItems.splice(index, 1);
          localStorage.setItem('shoppingList', JSON.stringify(shoppingItems));
      }
  }
  document.addEventListener('DOMContentLoaded', function() {
    // Load saved items
    const savedItems = JSON.parse(localStorage.getItem('shoppingList') || '[]');
    savedItems.forEach(item => {
        const li = document.createElement('li');
        li.className = 'shopping-item';
        li.innerHTML = `
            <span class="item">${item}</span>
            <button class="shopping-button" onclick="deleteShoppingItem(this)">Delete</button>
        `;
        document.getElementById('shopping-list').appendChild(li);
    });

    // Add event listener for the Enter key
    document.getElementById('shopping-input').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            addShoppingItem();
        }
    });
});