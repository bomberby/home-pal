
  // Shopping List Functions
  function addShoppingItem() {
      const input = document.getElementById('shopping-input');
      const list = document.getElementById('shopping-list');
      
      if (input.value.trim()) {
          fetch('/shopping-list-items', {
              method: 'POST',
              headers: {
                  'Content-Type': 'application/json'
              },
              body: JSON.stringify({
                  item_name: input.value.trim(),
                  quantity: 1,
                  purchased: false
              })
          })
          .then(response => response.json())
          .then(data => {
              const li = document.createElement('li');
              li.className = 'shopping-item';
              li.innerHTML = `
                  <span class="item">${data.item_name}</span>
                  <button class="shopping-button" onclick="deleteShoppingItem(${data.id})">Delete</button>
              `;
              list.appendChild(li);
          })
          .catch(error => console.error('Error:', error));
          
          input.value = '';
      }
  }

  function deleteShoppingItem(button) {
      button.parentElement.remove();
      
      fetch(`/shopping-list-items/${button.parentElement.dataset.id}`, {
          method: 'DELETE'
      })
      .then(response => response.json())
      .then(data => {
          button.parentElement.remove();
      })
      .catch(error => console.error('Error:', error));
  }
  document.addEventListener('DOMContentLoaded', function() {
      fetch('/shopping-list-items')
          .then(response => response.json())
          .then(data => {
              data.forEach(item => {
                  const li = document.createElement('li');
                  li.className = 'shopping-item';
                  li.dataset.id = item.id;
                  li.innerHTML = `
                      <span class="item">${item.item_name}</span>
                      <button class="shopping-button" onclick="deleteShoppingItem(this)">Delete</button>
                  `;
                  document.getElementById('shopping-list').appendChild(li);
              });
          })
          .catch(error => console.error('Error:', error));
      
      // Add event listener for the Enter key
      document.getElementById('shopping-input').addEventListener('keypress', function(e) {
          if (e.key === 'Enter') {
              addShoppingItem();
          }
      });
  });